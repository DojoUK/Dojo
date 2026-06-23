from datetime import date, datetime, timezone

from django.contrib import messages
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from dojo.mixins import OrgAdminMixin
from members.models import Member

from .models import Invoice, Payment


class InvoiceListView(OrgAdminMixin, ListView):
    template_name = 'billing/list.html'
    context_object_name = 'invoices'
    paginate_by = 50

    def get_queryset(self):
        qs = (
            Invoice.objects.filter(organisation=self.org)
            .select_related('member')
            .order_by('-created_at')
        )
        status = self.request.GET.get('status', '')
        if status in ('unpaid', 'paid', 'overdue'):
            qs = qs.filter(status=status)
        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(member__name__icontains=q)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base = Invoice.objects.filter(organisation=self.org)
        context['status_filter'] = self.request.GET.get('status', '')
        context['q'] = self.request.GET.get('q', '')
        context['total_unpaid'] = base.filter(status=Invoice.Status.UNPAID).aggregate(t=Sum('amount'))['t'] or 0
        context['total_paid'] = base.filter(status=Invoice.Status.PAID).aggregate(t=Sum('amount'))['t'] or 0
        context['count_unpaid'] = base.filter(status=Invoice.Status.UNPAID).count()
        context['count_overdue'] = sum(1 for inv in base.filter(status=Invoice.Status.UNPAID) if inv.is_overdue)
        return context


class InvoiceCreateView(OrgAdminMixin, View):
    def get(self, request, org_slug):
        members = Member.objects.filter(organisation=self.org, is_active=True).order_by('name')
        return render(request, 'billing/create.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'members': members,
            'today': date.today().isoformat(),
            'payment_methods': Payment.Method.choices,
        })

    def post(self, request, org_slug):
        members = Member.objects.filter(organisation=self.org, is_active=True).order_by('name')

        target = request.POST.get('target', 'one')
        member_ids = request.POST.getlist('member_ids') if target == 'all' else [request.POST.get('member_id')]
        amount = request.POST.get('amount', '').strip()
        period = request.POST.get('period', '').strip()
        due_date_raw = request.POST.get('due_date', '').strip()
        notes = request.POST.get('notes', '').strip()

        errors = []
        if not amount:
            errors.append('Amount is required.')
        if not period:
            errors.append('Period is required.')
        if not due_date_raw:
            errors.append('Due date is required.')
        if not member_ids or not any(member_ids):
            errors.append('Select at least one member.')

        if errors:
            for e in errors:
                messages.error(request, e)
            return render(request, 'billing/create.html', {
                'org': self.org,
                'org_membership': self.org_membership,
                'members': members,
                'today': date.today().isoformat(),
                'payment_methods': Payment.Method.choices,
            })

        try:
            due_date = date.fromisoformat(due_date_raw)
        except ValueError:
            messages.error(request, 'Invalid due date.')
            return redirect('invoice_create', org_slug=self.org.slug)

        if target == 'all':
            selected_members = Member.objects.filter(organisation=self.org, is_active=True)
        else:
            selected_members = Member.objects.filter(pk__in=[m for m in member_ids if m], organisation=self.org)

        created = 0
        for member in selected_members:
            Invoice.objects.create(
                organisation=self.org,
                member=member,
                amount=amount,
                period=period,
                due_date=due_date,
                notes=notes,
            )
            created += 1

        messages.success(request, f'{created} invoice{"s" if created != 1 else ""} created.')
        return redirect('invoice_list', org_slug=self.org.slug)


class InvoiceDetailView(OrgAdminMixin, DetailView):
    template_name = 'billing/detail.html'
    context_object_name = 'invoice'

    def get_object(self):
        return get_object_or_404(Invoice, pk=self.kwargs['pk'], organisation=self.org)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['payments'] = self.object.payments.all()
        context['payment_methods'] = Payment.Method.choices
        context['amount_paid'] = self.object.payments.aggregate(t=Sum('amount'))['t'] or 0
        return context


class MarkPaidView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        invoice = get_object_or_404(Invoice, pk=pk, organisation=self.org)
        invoice.status = Invoice.Status.PAID
        invoice.save(update_fields=['status'])
        messages.success(request, 'Invoice marked as paid.')
        return redirect('invoice_detail', org_slug=self.org.slug, pk=pk)


class MarkUnpaidView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        invoice = get_object_or_404(Invoice, pk=pk, organisation=self.org)
        invoice.status = Invoice.Status.UNPAID
        invoice.save(update_fields=['status'])
        messages.success(request, 'Invoice marked as unpaid.')
        return redirect('invoice_detail', org_slug=self.org.slug, pk=pk)


class RecordPaymentView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        invoice = get_object_or_404(Invoice, pk=pk, organisation=self.org)
        amount_raw = request.POST.get('amount', '').strip()
        method = request.POST.get('method', Payment.Method.MANUAL)
        notes = request.POST.get('notes', '').strip()
        paid_at_raw = request.POST.get('paid_at', '').strip()

        try:
            amount = float(amount_raw)
        except (ValueError, TypeError):
            messages.error(request, 'Invalid amount.')
            return redirect('invoice_detail', org_slug=self.org.slug, pk=pk)

        try:
            paid_at = datetime.fromisoformat(paid_at_raw).replace(tzinfo=timezone.utc) if paid_at_raw else datetime.now(tz=timezone.utc)
        except ValueError:
            paid_at = datetime.now(tz=timezone.utc)

        Payment.objects.create(
            invoice=invoice,
            amount=amount,
            method=method,
            notes=notes,
            paid_at=paid_at,
        )

        total_paid = invoice.payments.aggregate(t=Sum('amount'))['t'] or 0
        if total_paid >= invoice.amount:
            invoice.status = Invoice.Status.PAID
            invoice.save(update_fields=['status'])

        messages.success(request, f'Payment of £{amount:.2f} recorded.')
        return redirect('invoice_detail', org_slug=self.org.slug, pk=pk)


class BillingExportView(OrgAdminMixin, View):
    def get(self, request, org_slug):
        import csv
        from django.http import HttpResponse
        invoices = (
            Invoice.objects.filter(organisation=self.org)
            .select_related('member')
            .prefetch_related('payments')
            .order_by('-created_at')
        )
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{self.org.slug}-billing.csv"'
        writer = csv.writer(response)
        writer.writerow(['Member', 'Period', 'Amount', 'Due date', 'Status', 'Paid at', 'Payment method'])
        for inv in invoices:
            payment = inv.payments.order_by('-paid_at').first()
            writer.writerow([
                inv.member.name,
                inv.period,
                str(inv.amount),
                inv.due_date.isoformat(),
                inv.get_status_display(),
                payment.paid_at.strftime('%Y-%m-%d') if payment else '',
                payment.method if payment else '',
            ])
        return response


class ChaseOverdueView(OrgAdminMixin, View):
    def post(self, request, org_slug):
        from .emails import send_invoice_email
        overdue = [
            inv for inv in Invoice.objects.filter(organisation=self.org, status=Invoice.Status.UNPAID)
            if inv.is_overdue
        ]
        sent, skipped = 0, 0
        for inv in overdue:
            try:
                ok, _ = send_invoice_email(inv, request=request)
                if ok:
                    sent += 1
                else:
                    skipped += 1
            except Exception:
                skipped += 1

        if sent:
            messages.success(request, f'Chased {sent} overdue invoice{"s" if sent != 1 else ""}.')
        if skipped:
            messages.warning(request, f'{skipped} invoice{"s" if skipped != 1 else ""} skipped — no email address on file.')
        if not overdue:
            messages.info(request, 'No overdue invoices to chase.')
        return redirect('invoice_list', org_slug=self.org.slug)


class BulkInvoiceView(OrgAdminMixin, View):
    template_name = 'billing/bulk_invoice.html'

    def _default_period(self):
        from datetime import date
        import calendar
        today = date.today()
        # Default to next month so you're billing ahead
        if today.month == 12:
            return date(today.year + 1, 1, 1).strftime('%B %Y')
        return date(today.year, today.month + 1, 1).strftime('%B %Y')

    def _default_due_date(self):
        from datetime import date
        import calendar
        today = date.today()
        if today.month == 12:
            last_day = calendar.monthrange(today.year + 1, 1)[1]
            return date(today.year + 1, 1, last_day).isoformat()
        last_day = calendar.monthrange(today.year, today.month + 1)[1]
        return date(today.year, today.month + 1, last_day).isoformat()

    def _member_rows(self, org, period):
        members = (
            Member.objects.filter(organisation=org, is_active=True, monthly_fee__isnull=False)
            .exclude(monthly_fee=0)
            .order_by('name')
        )
        existing_periods = set(
            Invoice.objects.filter(organisation=org, period=period, member__in=members)
            .values_list('member_id', flat=True)
        )
        rows = []
        for m in members:
            rows.append({
                'member': m,
                'amount': m.monthly_fee,
                'has_subscription': m.has_active_subscription,
                'already_invoiced': m.pk in existing_periods,
            })
        return rows

    def get(self, request, org_slug):
        period = request.GET.get('period', self._default_period())
        rows = self._member_rows(self.org, period)
        return render(request, self.template_name, {
            'org': self.org,
            'org_membership': self.org_membership,
            'rows': rows,
            'period': period,
            'due_date': self._default_due_date(),
        })

    def post(self, request, org_slug):
        period = request.POST.get('period', '').strip()
        due_date_raw = request.POST.get('due_date', '').strip()
        send_emails = request.POST.get('send_emails') == '1'
        selected_ids = set(request.POST.getlist('member_ids'))

        if not period or not due_date_raw or not selected_ids:
            messages.error(request, 'Period, due date, and at least one member are required.')
            return redirect('invoice_bulk', org_slug=self.org.slug)

        try:
            due_date = date.fromisoformat(due_date_raw)
        except ValueError:
            messages.error(request, 'Invalid due date.')
            return redirect('invoice_bulk', org_slug=self.org.slug)

        members = Member.objects.filter(
            pk__in=selected_ids, organisation=self.org,
            monthly_fee__isnull=False,
        )

        # Skip members who already have an invoice for this period
        already = set(
            Invoice.objects.filter(organisation=self.org, period=period, member__in=members)
            .values_list('member_id', flat=True)
        )

        created_invoices = []
        for member in members:
            if member.pk in already:
                continue
            inv = Invoice.objects.create(
                organisation=self.org,
                member=member,
                amount=member.monthly_fee,
                period=period,
                due_date=due_date,
            )
            created_invoices.append(inv)

        skipped = len(already.intersection({int(i) for i in selected_ids}))
        messages.success(
            request,
            f'{len(created_invoices)} invoice{"s" if len(created_invoices) != 1 else ""} created'
            + (f', {skipped} skipped (already invoiced).' if skipped else '.'),
        )

        if send_emails and created_invoices:
            from .emails import send_invoice_email
            sent = 0
            for inv in created_invoices:
                try:
                    ok, _ = send_invoice_email(inv, request=request)
                    if ok:
                        sent += 1
                except Exception:
                    pass
            if sent:
                messages.success(request, f'{sent} invoice email{"s" if sent != 1 else ""} sent.')

        return redirect('invoice_list', org_slug=self.org.slug)


class SendInvoiceEmailView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        invoice = get_object_or_404(Invoice, pk=pk, organisation=self.org)
        from .emails import send_invoice_email
        try:
            ok, result = send_invoice_email(invoice, request=request)
            if ok:
                messages.success(request, f'Invoice emailed to {result}.')
            else:
                messages.error(request, f'Could not send email: {result}')
        except Exception as e:
            messages.error(request, f'Email failed: {e}')
        return redirect('invoice_detail', org_slug=self.org.slug, pk=pk)


class SendReminderEmailView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        invoice = get_object_or_404(Invoice, pk=pk, organisation=self.org)
        from .emails import send_reminder_email
        try:
            ok, result = send_reminder_email(invoice, request=request)
            if ok:
                messages.success(request, f'Payment reminder sent to {result}.')
            else:
                messages.error(request, f'Could not send reminder: {result}')
        except Exception as e:
            messages.error(request, f'Reminder failed: {e}')
        return redirect('invoice_detail', org_slug=self.org.slug, pk=pk)
