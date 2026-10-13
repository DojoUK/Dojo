"""The jobs the `scheduler` container runs, and how often (in seconds)."""
from datetime import timedelta


def _collect_automatic_payments():
    from billing.autopay import collect_due
    return collect_due()


JOBS = (
    ('collect_automatic_payments', 60, _collect_automatic_payments),
)


def last_run(name):
    from .models import JobRun
    run = JobRun.objects.filter(name=name).first()
    return run.last_finished_at if run else None


def is_running(name='collect_automatic_payments', within=timedelta(minutes=10)):
    """Whether the scheduler has finished `name` recently."""
    from django.utils import timezone
    finished = last_run(name)
    return finished is not None and timezone.now() - finished < within
