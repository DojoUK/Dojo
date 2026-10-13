import logging
import time
import traceback

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from jobs.models import JobRun
from jobs.registry import JOBS

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Run Dojo\'s scheduled jobs (automatic payments) until stopped. The scheduler container runs this.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Run every job once, then stop.')

    def handle(self, *args, **options):
        next_run = {name: 0.0 for name, _, _ in JOBS}
        self.stdout.write('Scheduler started.')
        while True:
            for name, every, job in JOBS:
                if options['once'] or time.monotonic() >= next_run[name]:
                    self.run(name, job, long_running=not options['once'])
                    next_run[name] = time.monotonic() + every
            if options['once']:
                return
            time.sleep(5)

    def run(self, name, job, long_running=True):
        if long_running:
            close_old_connections()  # the database may have dropped an idle connection between runs
        run, _ = JobRun.objects.get_or_create(name=name)
        run.last_started_at = timezone.now()
        try:
            result = job()
        except Exception:  # one failing job mustn't stop the others; the error is kept for Settings
            run.last_error = traceback.format_exc()[-2000:]
            log.exception('Scheduled job %s failed', name)
        else:
            run.last_error = ''
            if result:
                self.stdout.write(f'{name}: {result}')
        run.last_finished_at = timezone.now()
        run.save()
