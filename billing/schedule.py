"""
Automatic invoices: each plan's schedule (InvoiceSchedule) says when Dojo makes
its invoices. The scheduler container calls create_due_runs() and
send_due_reminders() regularly; both are safe to run any number of times.

A run made in "let me check first" mode is held: its invoices aren't shown to
members, emailed, charged or chased until an admin sends them (release()).
"""
import calendar
import logging
from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

log = logging.getLogger(__name__)

HELD = 'awaiting_review'


def _clamp_day(year, month, day):
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def members_for_policy(org, policy):
    """Active members billed on `policy`: their own plan, or their class's when they have none."""
    from members.models import Member
    direct = list(Member.objects.filter(organisation=org, is_active=True, billing_policy=policy))
    via_class = Member.objects.filter(
        organisation=org, is_active=True, billing_policy__isnull=True,
        enrolments__assigned_class__billing_policy=policy,
    ).distinct()
    seen, members = set(), []
    for member in direct + list(via_class):
        if member.pk not in seen:
            seen.add(member.pk)
            members.append(member)
    return members


def due_periods(schedule, today):
    """Periods whose invoice date has arrived: (label, invoice_date, term, year, month)."""
    from .models import BillingPolicy, OrgTerm
    policy = schedule.policy
    cycle = policy.billing_cycle
    if cycle == BillingPolicy.BillingCycle.MONTHLY:
        invoice_date = _clamp_day(today.year, today.month, schedule.day_of_month)
        if invoice_date <= today:
            return [(invoice_date.strftime('%B %Y'), invoice_date, None, today.year, today.month)]
    elif cycle == BillingPolicy.BillingCycle.TERMLY:
        periods = []
        for term in OrgTerm.objects.filter(organisation=policy.organisation, end_date__gte=today).order_by('start_date'):
            invoice_date = term.start_date - timedelta(days=schedule.days_before_term)
            if invoice_date <= today:
                periods.append((term.name, invoice_date, term, None, None))
        return periods
    elif cycle == BillingPolicy.BillingCycle.ANNUAL:
        invoice_date = _clamp_day(today.year, schedule.annual_month, schedule.annual_day)
        if invoice_date <= today:
            return [(f'{policy.name} {today.year}', invoice_date, None, None, None)]
    return []


def next_run(schedule, today=None):
    """When the plan's next invoices will be made, for the settings page (None if never)."""
    from .models import BillingPolicy, InvoiceRun, OrgTerm
    today = today or timezone.localdate()
    earliest = max(today, schedule.starts_from or today)
    done = set(InvoiceRun.objects.filter(schedule=schedule).values_list('period', flat=True))
    cycle = schedule.policy.billing_cycle
    if cycle == BillingPolicy.BillingCycle.MONTHLY:
        year, month = earliest.year, earliest.month
        for _ in range(13):
            candidate = _clamp_day(year, month, schedule.day_of_month)
            if candidate >= earliest and candidate.strftime('%B %Y') not in done:
                return candidate, candidate.strftime('%B %Y')
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    elif cycle == BillingPolicy.BillingCycle.TERMLY:
        for term in OrgTerm.objects.filter(organisation=schedule.policy.organisation,
                                           end_date__gte=today).order_by('start_date'):
            if term.name not in done:
                return max(term.start_date - timedelta(days=schedule.days_before_term), earliest), term.name
    elif cycle == BillingPolicy.BillingCycle.ANNUAL:
        for year in (earliest.year, earliest.year + 1):
            candidate = _clamp_day(year, schedule.annual_month, schedule.annual_day)
            label = f'{schedule.policy.name} {year}'
            if candidate >= earliest and label not in done:
                return candidate, label
    return None


def estimate(schedule, period_info=None):
    """(number of invoices, total) the next run would make, using today's plans and prices."""
    from .calculator import calculate_invoice_amount
    policy = schedule.policy
    count, total = 0, Decimal(0)
    for member in members_for_policy(policy.organisation, policy):
        if member.has_active_subscription:
            continue
        net = calculate_invoice_amount(member, policy)['net']
        if net > 0:
            count += 1
            total += Decimal(net)
    return count, total


def create_due_runs(today=None):
    """Make any automatic invoices that are due. Returns how many runs were made."""
    from .models import InvoiceRun, InvoiceSchedule
    today = today or timezone.localdate()
    made = 0
    schedules = InvoiceSchedule.objects.filter(enabled=True, policy__is_active=True).select_related('policy__organisation')
    for schedule in schedules:
        if not schedule.policy.organisation.has_feature('billing'):
            continue
        for period, invoice_date, term, year, month in due_periods(schedule, today):
            if schedule.starts_from and invoice_date < schedule.starts_from:
                continue
            if InvoiceRun.objects.filter(schedule=schedule, period=period).exists():
                continue
            try:
                create_run(schedule, period, invoice_date, term, year, month)
                made += 1
            except IntegrityError:
                continue  # another scheduler made this run first
    return made


def create_run(schedule, period, invoice_date, term=None, year=None, month=None):
    from .calculator import calculate_invoice_amount
    from .models import Invoice, InvoiceRun
    policy = schedule.policy
    org = policy.organisation
    review = schedule.send_mode == schedule.SendMode.REVIEW
    with transaction.atomic():
        run = InvoiceRun.objects.create(schedule=schedule, period=period,
                                        status=HELD if review else InvoiceRun.Status.SENT,
                                        released_at=None if review else timezone.now())
        already = set(Invoice.objects.filter(organisation=org, period=period).values_list('member_id', flat=True))
        total = Decimal(0)
        count = 0
        for member in members_for_policy(org, policy):
            # Members still on an old Stripe subscription are already paying through it.
            if member.pk in already or member.has_active_subscription:
                continue
            calc = calculate_invoice_amount(member, policy, term=term, year=year, month=month)
            if calc['net'] <= 0:
                continue
            Invoice.objects.create(
                organisation=org, member=member, billing_policy=policy, amount=calc['net'],
                discount_amount=calc['discount'], period=period,
                due_date=invoice_date + timedelta(days=schedule.due_after_days), run=run,
            )
            count += 1
            total += Decimal(calc['net'])
        run.invoice_count, run.total = count, total
        run.save(update_fields=['invoice_count', 'total'])
        if review:
            transaction.on_commit(lambda: notify_admins(run))
        else:
            transaction.on_commit(lambda: email_invoices(run))
    return run


def release(run):
    """Send a held run: queue automatic payments and email every invoice."""
    from . import autopay
    from .models import InvoiceRun
    if run.status != HELD:
        return 0
    run.status = InvoiceRun.Status.SENT
    run.released_at = timezone.now()
    run.save(update_fields=['status', 'released_at'])
    for invoice in run.invoices.filter(status='unpaid'):
        autopay.mark_due(invoice)
    return email_invoices(run)


def email_invoices(run):
    from .emails import send_invoice_email
    sent = 0
    for invoice in run.invoices.filter(status='unpaid').select_related('member', 'organisation'):
        invoice.refresh_from_db()
        try:
            if send_invoice_email(invoice)[0]:
                sent += 1
        except Exception:  # one bad address mustn't stop the rest of the run
            log.exception('Could not email invoice %s from run %s', invoice.pk, run.pk)
    return sent


def cancel(run):
    """Throw away a held run's invoices. The run is kept, so the scheduler doesn't make it again."""
    from .models import InvoiceRun
    if run.status != HELD:
        return 0
    deleted = run.invoices.filter(status='unpaid', payments__isnull=True).delete()[0]
    run.status = InvoiceRun.Status.CANCELLED
    run.save(update_fields=['status'])
    return deleted


def notify_admins(run):
    """Tell the club's admins a run is waiting for them."""
    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives
    from django.urls import reverse

    from mailer import connection_for
    from organisations.models import OrganisationMember
    org = run.schedule.policy.organisation
    recipients = list(OrganisationMember.objects.filter(organisation=org, role='org_admin')
                      .exclude(user__email='').values_list('user__email', flat=True))
    if not recipients:
        return
    link = settings.SITE_URL.rstrip('/') + reverse('invoice_run', kwargs={'org_slug': org.slug, 'pk': run.pk})
    text = (f'{run.invoice_count} invoices for {run.schedule.policy.name}, {run.period} (£{run.total:.2f} in all) '
            f'are ready. Nothing has been sent to members yet.\n\nCheck and send them: {link}\n\n'
            f'Powered by Dojo')
    try:
        EmailMultiAlternatives(f'{run.invoice_count} invoices ready to check: {run.period}', text, to=recipients,
                               connection=connection_for(org)).send()
    except Exception:  # the run is safely waiting on the billing page either way
        log.exception('Could not tell admins about invoice run %s', run.pk)


def reminder_settings(org):
    stored = (org.settings or {}).get('overdue_reminders') or {}
    return {'enabled': bool(stored.get('enabled')), 'after_days': int(stored.get('after_days', 7)),
            'repeat_days': int(stored.get('repeat_days', 14))}


def send_due_reminders(today=None):
    """Chase overdue invoices for clubs that have switched automatic reminders on."""
    from django.db.models import Q

    from organisations.models import Organisation

    from .emails import send_reminder_email
    from .models import Invoice
    today = today or timezone.localdate()
    sent = 0
    for org in Organisation.objects.all():
        options = reminder_settings(org)
        if not options['enabled'] or not org.has_feature('billing'):
            continue
        invoices = (
            Invoice.objects.filter(organisation=org, status='unpaid', member__is_active=True,
                                   due_date__lte=today - timedelta(days=options['after_days']))
            .filter(Q(reminder_sent_at__isnull=True)
                    | Q(reminder_sent_at__lte=timezone.now() - timedelta(days=options['repeat_days'])))
            .exclude(run__status=HELD)
            .exclude(autopay_status__in=['due', 'collecting', 'processing'])
            .select_related('member', 'organisation')
        )
        for invoice in invoices:
            try:
                if send_reminder_email(invoice)[0]:
                    sent += 1
            except Exception:  # one bad address mustn't stop the other reminders
                log.exception('Could not send reminder for invoice %s', invoice.pk)
    return sent
