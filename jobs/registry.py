"""The jobs the `scheduler` container runs, and how often (in seconds)."""
from datetime import timedelta


def _collect_automatic_payments():
    from billing.autopay import collect_due
    return collect_due()


def _create_scheduled_invoices():
    from billing.schedule import create_due_runs
    return create_due_runs()


def _send_overdue_reminders():
    from billing.schedule import send_due_reminders
    return send_due_reminders()


def _send_session_reminders():
    from classes.reminders import send_due
    return send_due()


def _send_review_prompts():
    from network.reviews import send_prompts
    return send_prompts()


def _send_weekly_summaries():
    from organisations.weekly_summary import send_due
    return send_due()


JOBS = (
    ('collect_automatic_payments', 60, _collect_automatic_payments),
    # Both are safe to repeat: a period's invoices are made once, and reminders respect their repeat gap.
    ('create_scheduled_invoices', 15 * 60, _create_scheduled_invoices),
    ('send_overdue_reminders', 60 * 60, _send_overdue_reminders),
    ('send_session_reminders', 10 * 60, _send_session_reminders),
    ('send_review_prompts', 60 * 60, _send_review_prompts),
    ('send_weekly_summaries', 30 * 60, _send_weekly_summaries),
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
