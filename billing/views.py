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
