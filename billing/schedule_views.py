"""Billing → Automatic invoices: each plan's schedule, runs waiting to be checked, and reminders."""
from datetime import timedelta

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from jobs.registry import is_running

from . import schedule as scheduling
from .models import BillingPolicy, InvoiceRun, InvoiceSchedule, OrgTerm
from .views import BillingManagerMixin

MONTHS = [(n, timezone.datetime(2000, n, 1).strftime('%B')) for n in range(1, 13)]


def _int(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


class AutomaticInvoicesView(BillingManagerMixin, View):
    template_name = 'billing/automatic.html'

    def get(self, request, org_slug):
        policies = BillingPolicy.objects.filter(organisation=self.org, is_active=True).order_by('name')
        plans = []
        for policy in policies:
            plan_schedule = InvoiceSchedule.objects.filter(policy=policy).first() or InvoiceSchedule(policy=policy)
            upcoming = None
            if plan_schedule.pk and plan_schedule.enabled:
                upcoming = scheduling.next_run(plan_schedule)
            count, total = scheduling.estimate(plan_schedule) if upcoming else (0, 0)
            plans.append({'policy': policy, 'schedule': plan_schedule, 'next': upcoming, 'count': count, 'total': total})
        runs = InvoiceRun.objects.filter(schedule__policy__organisation=self.org).select_related('schedule__policy')
        return render(request, self.template_name, {
            'org': self.org, 'org_membership': self.org_membership,
            'plans': plans,
            'held_runs': runs.filter(status=scheduling.HELD),
            'history': runs[:20],
            'has_terms': OrgTerm.objects.filter(organisation=self.org, end_date__gte=timezone.localdate()).exists(),
            'reminders': scheduling.reminder_settings(self.org),
            'months': MONTHS, 'days': range(1, 29),
            'scheduler_running': is_running('create_scheduled_invoices', within=timedelta(minutes=40)),
            'any_enabled': any(plan['schedule'].enabled for plan in plans),
        })

    def post(self, request, org_slug):
        if request.POST.get('action') == 'reminders':
            self.org.settings = {**(self.org.settings or {}), 'overdue_reminders': {
                'enabled': bool(request.POST.get('enabled')),
                'after_days': _int(request.POST.get('after_days'), 7, 1, 90),
                'repeat_days': _int(request.POST.get('repeat_days'), 14, 1, 90),
            }}
            self.org.save(update_fields=['settings'])
            messages.success(request, 'Reminder settings saved.')
            return redirect('billing_automatic', org_slug=self.org.slug)

        policy = get_object_or_404(BillingPolicy, pk=request.POST.get('policy'), organisation=self.org)
        plan_schedule = InvoiceSchedule.objects.filter(policy=policy).first() or InvoiceSchedule(policy=policy)
        was_enabled = plan_schedule.pk and plan_schedule.enabled
        plan_schedule.enabled = bool(request.POST.get('enabled')) and policy.billing_cycle != 'custom'
        plan_schedule.day_of_month = _int(request.POST.get('day_of_month'), 1, 1, 28)
        plan_schedule.days_before_term = _int(request.POST.get('days_before_term'), 7, 0, 90)
        plan_schedule.annual_month = _int(request.POST.get('annual_month'), 9, 1, 12)
        plan_schedule.annual_day = _int(request.POST.get('annual_day'), 1, 1, 28)
        plan_schedule.due_after_days = _int(request.POST.get('due_after_days'), 14, 0, 90)
        mode = request.POST.get('send_mode')
        plan_schedule.send_mode = mode if mode in dict(InvoiceSchedule.SendMode.choices) else InvoiceSchedule.SendMode.REVIEW
        if plan_schedule.enabled and not was_enabled:
            # Switching on mid-period waits for the next invoice date rather than billing one that's passed.
            plan_schedule.starts_from = timezone.localdate()
        plan_schedule.save()
        messages.success(request, f'Automatic invoices for {policy.name} {"switched on" if plan_schedule.enabled else "saved (off)"}.')
        return redirect(f"{request.path}#plan-{policy.pk}")


class InvoiceRunView(BillingManagerMixin, View):
    template_name = 'billing/run.html'

    def get_run(self, pk):
        return get_object_or_404(InvoiceRun.objects.select_related('schedule__policy'), pk=pk,
                                 schedule__policy__organisation=self.org)

    def get(self, request, org_slug, pk):
        run = self.get_run(pk)
        return render(request, self.template_name, {
            'org': self.org, 'org_membership': self.org_membership, 'run': run,
            'invoices': run.invoices.select_related('member').order_by('member__name'),
        })

    def post(self, request, org_slug, pk):
        run = self.get_run(pk)
        action = request.POST.get('action')
        if run.status != scheduling.HELD:
            messages.error(request, 'These invoices have already been dealt with.')
        elif action == 'send':
            emailed = scheduling.release(run)
            messages.success(request, f'Sent: {run.invoice_count} invoices are live, and {emailed} emailed.')
        elif action == 'cancel':
            deleted = scheduling.cancel(run)
            messages.success(request, f'Cancelled: {deleted} invoices deleted. Nothing was sent.')
        return redirect('invoice_run', org_slug=self.org.slug, pk=run.pk)
