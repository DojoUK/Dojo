"""
Purge MemberApplication records that no longer need to be kept:

  - rejected applications, 90+ days after the decision
  - approved applications, 30+ days after the decision (the data has
    already been copied onto the Member/Guardian/SignedWaiver records
    by then, so the source application is redundant)

Pending applications are never touched.

Run with: python manage.py purge_stale_applications
Add --dry-run to list what would be deleted without changing anything.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from members.models import MemberApplication

REJECTED_RETENTION = timedelta(days=90)
APPROVED_RETENTION = timedelta(days=30)


class Command(BaseCommand):
    help = 'Purge rejected and approved MemberApplication records past their retention window'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='List candidates without changing anything')

    def handle(self, *args, **options):
        now = timezone.now()
        candidates = MemberApplication.objects.filter(
            status=MemberApplication.Status.REJECTED,
            decided_at__lt=now - REJECTED_RETENTION,
        ) | MemberApplication.objects.filter(
            status=MemberApplication.Status.APPROVED,
            decided_at__lt=now - APPROVED_RETENTION,
        )

        if not candidates.exists():
            self.stdout.write('No stale applications past their retention window.')
            return

        count = candidates.count()
        for app in candidates:
            label = f'{app.name} (#{app.pk}, {app.organisation}) — {app.get_status_display()} {app.decided_at:%d %b %Y}'
            if options['dry_run']:
                self.stdout.write(f'Would delete: {label}')
            else:
                self.stdout.write(self.style.SUCCESS(f'Deleted: {label}'))

        if options['dry_run']:
            self.stdout.write(self.style.WARNING(f'{count} application(s) would be deleted. Re-run without --dry-run to apply.'))
        else:
            candidates.delete()
            self.stdout.write(self.style.SUCCESS(f'Done — {count} application(s) deleted.'))
