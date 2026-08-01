from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timezone

from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from dojo.mixins import OrgAdminMixin
from members.models import Member
from inventory.models import Product, ProductVariant, StockMovement

from .models import Invoice, InvoiceItem, Payment, BillingPolicy, OrgTerm, PolicyDiscount, MemberDiscount


class InsufficientStockError(Exception):
    """Raised inside an invoice-creation transaction to abort it and roll back any writes
    made so far when one or more product line items can't be fully stocked."""
    def __init__(self, errors):
        self.errors = errors
        super().__init__('; '.join(errors))


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
    def _products_context(self):
        products = (
            Product.objects.filter(organisation=self.org, is_active=True)
            .prefetch_related('variants')
            .order_by('name')
        )
        return products

    def _render_form(self, request, **extra):
        members = Member.objects.filter(organisation=self.org, is_active=True).order_by('name')
        context = {
            'org': self.org,
            'org_membership': self.org_membership,
            'members': members,
            'today': date.today().isoformat(),
            'payment_methods': Payment.Method.choices,
            'products': self._products_context(),
        }
        context.update(extra)
        return render(request, 'billing/create.html', context)

    def get(self, request, org_slug):
        return self._render_form(request)

    def _parse_items(self, request, errors):
        """Read the product-line-item rows from POST. Returns a list of (ProductVariant, quantity)."""
        variant_ids = request.POST.getlist('item_variant_id')
        quantities = request.POST.getlist('item_quantity')
        item_rows = []
        for variant_id_raw, quantity_raw in zip(variant_ids, quantities):
            variant_id_raw = variant_id_raw.strip()
            quantity_raw = quantity_raw.strip()
            if not variant_id_raw:
                continue
            try:
                quantity = int(quantity_raw)
            except ValueError:
                errors.append('Product quantities must be whole numbers.')
                continue
            if quantity <= 0:
                continue
            try:
                variant = ProductVariant.objects.select_related('product').get(
                    pk=variant_id_raw, product__organisation=self.org,
                )
            except ProductVariant.DoesNotExist:
                errors.append('One of the selected products could not be found.')
                continue
            item_rows.append((variant, quantity))
        return item_rows

    def post(self, request, org_slug):
        target = request.POST.get('target', 'one')
        member_ids = request.POST.getlist('member_ids') if target == 'all' else [request.POST.get('member_id')]
        amount = request.POST.get('amount', '').strip()
        period = request.POST.get('period', '').strip()
        due_date_raw = request.POST.get('due_date', '').strip()
        notes = request.POST.get('notes', '').strip()

        errors = []
        item_rows = self._parse_items(request, errors)

        if not amount and not item_rows:
            errors.append('Enter an amount, add at least one product, or both.')
        if not period:
            errors.append('Period is required.')
        if not due_date_raw:
            errors.append('Due date is required.')
        if not member_ids or not any(member_ids):
            errors.append('Select at least one member.')
        if item_rows and target == 'all':
            errors.append(
                'Product line items can only be added when invoicing a single specific member — '
                'switch to "Specific member" or create separate invoices for product sales.'
            )

        try:
            base_amount = Decimal(amount) if amount else Decimal('0')
            if base_amount < 0:
                errors.append('Amount can\'t be negative.')
        except (InvalidOperation, ValueError):
            errors.append('Invalid amount.')
            base_amount = Decimal('0')

        if errors:
            for e in errors:
                messages.error(request, e)
            return self._render_form(request)

        try:
            due_date = date.fromisoformat(due_date_raw)
        except ValueError:
            messages.error(request, 'Invalid due date.')
            return self._render_form(request)

        if target == 'all':
            selected_members = list(Member.objects.filter(organisation=self.org, is_active=True))
        else:
            selected_members = list(
                Member.objects.filter(pk__in=[m for m in member_ids if m], organisation=self.org)
            )

        if not selected_members:
            messages.error(request, 'No matching members found.')
            return self._render_form(request)

        try:
            with transaction.atomic():
                # Lock every distinct variant involved, in a stable order, so two concurrent
                # invoices can't both read the same stock level and both succeed when only
                # one has enough stock.
                variant_ids = sorted({variant.pk for variant, _ in item_rows})
                locked_variants = {
                    v.pk: v for v in ProductVariant.objects.select_for_update().filter(pk__in=variant_ids)
                }

                stock_errors = []
                for variant, quantity in item_rows:
                    locked = locked_variants[variant.pk]
                    if quantity > locked.quantity_in_stock:
                        stock_errors.append(
                            f'Not enough stock for {locked.product.name} ({locked.size}): '
                            f'requested {quantity}, only {locked.quantity_in_stock} available.'
                        )
                if stock_errors:
                    raise InsufficientStockError(stock_errors)

                # Use the freshly-locked price (not the value read before locking) so the
                # invoice total always matches the sum of its line items exactly.
                items_total = sum(
                    (locked_variants[variant.pk].price * quantity for variant, quantity in item_rows),
                    Decimal('0'),
                )

                invoice = Invoice.objects.create(
                    organisation=self.org,
                    member=selected_members[0],
                    amount=base_amount + items_total,
                    period=period,
                    due_date=due_date,
                    notes=notes,
                )
                for variant, quantity in item_rows:
                    locked = locked_variants[variant.pk]
                    InvoiceItem.objects.create(
                        invoice=invoice,
                        variant=locked,
                        quantity=quantity,
                        unit_price=locked.price,
                    )
                    locked.adjust_stock(
                        -quantity,
                        reason=StockMovement.Reason.SALE,
                        invoice=invoice,
                        notes=f'Sold on invoice #{invoice.pk}',
                        user=request.user,
                    )
                created_invoices = [invoice]

                # Remaining selected members (only reachable when there are no product items,
                # since 'all' + products is rejected above) each get their own plain invoice.
                for member in selected_members[1:]:
                    created_invoices.append(Invoice.objects.create(
                        organisation=self.org,
                        member=member,
                        amount=base_amount,
                        period=period,
                        due_date=due_date,
                        notes=notes,
                    ))
        except InsufficientStockError as e:
            for msg in e.errors:
                messages.error(request, msg)
            messages.error(request, 'No invoice was created — fix the stock issue above and try again.')
            return self._render_form(request)

        created = len(created_invoices)
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

    def _default_due_date(self):
        import calendar
        today = date.today()
        if today.month == 12:
            last_day = calendar.monthrange(today.year + 1, 1)[1]
            return date(today.year + 1, 1, last_day).isoformat()
        last_day = calendar.monthrange(today.year, today.month + 1)[1]
        return date(today.year, today.month + 1, last_day).isoformat()

    def _resolve_period(self, request):
        """Parse period info from GET/POST: returns (term_obj_or_None, year, month, period_label)."""
        term_pk = request.POST.get('term_id') or request.GET.get('term_id')
        year_raw = request.POST.get('year') or request.GET.get('year')
        month_raw = request.POST.get('month') or request.GET.get('month')

        term = None
        year = month = None
        period_label = request.POST.get('period') or request.GET.get('period', '')

        if term_pk:
            try:
                from .models import OrgTerm
                term = OrgTerm.objects.get(pk=term_pk, organisation=self.org)
                period_label = term.name
            except OrgTerm.DoesNotExist:
                pass
        elif year_raw and month_raw:
            try:
                year, month = int(year_raw), int(month_raw)
                period_label = date(year, month, 1).strftime('%B %Y')
            except (ValueError, TypeError):
                pass

        return term, year, month, period_label

    def _member_rows(self, policies, term, year, month, period_label):
        """Build preview rows for all policy-assigned members."""
        from .calculator import calculate_invoice_amount

        rows = []
        already_invoiced_ids = set()
        if period_label:
            already_invoiced_ids = set(
                Invoice.objects.filter(organisation=self.org, period=period_label)
                .values_list('member_id', flat=True)
            )

        for policy in policies:
            # Members with this policy directly
            direct = list(Member.objects.filter(
                organisation=self.org, is_active=True, billing_policy=policy
            ))
            # Members whose enrolled class uses this policy (and they have no member-level override)
            from classes.models import ClassMember
            class_assigned = list(Member.objects.filter(
                organisation=self.org, is_active=True,
                billing_policy__isnull=True,
                enrolments__assigned_class__billing_policy=policy,
            ).distinct())

            seen = set()
            for member in direct + class_assigned:
                if member.pk in seen:
                    continue
                seen.add(member.pk)
                calc = calculate_invoice_amount(member, policy, term=term, year=year, month=month)
                rows.append({
                    'member': member,
                    'policy': policy,
                    'gross': calc['gross'],
                    'discount': calc['discount'],
                    'amount': calc['net'],
                    'breakdown': calc['breakdown'],
                    'discount_lines': calc['discount_lines'],
                    'has_subscription': member.has_active_subscription,
                    'already_invoiced': member.pk in already_invoiced_ids,
                })

        # Legacy: members with monthly_fee and no policy
        legacy = Member.objects.filter(
            organisation=self.org, is_active=True,
            billing_policy__isnull=True,
            monthly_fee__isnull=False,
        ).exclude(monthly_fee=0)
        for member in legacy:
            if any(r['member'].pk == member.pk for r in rows):
                continue
            rows.append({
                'member': member,
                'policy': None,
                'gross': member.monthly_fee,
                'discount': 0,
                'amount': member.monthly_fee,
                'breakdown': [{'label': 'Legacy monthly fee', 'amount': member.monthly_fee}],
                'discount_lines': [],
                'has_subscription': member.has_active_subscription,
                'already_invoiced': member.pk in already_invoiced_ids,
            })

        rows.sort(key=lambda r: (r.get('policy') is None, r['member'].name))
        return rows

    def get(self, request, org_slug):
        policies = BillingPolicy.objects.filter(organisation=self.org, is_active=True)
        terms = OrgTerm.objects.filter(organisation=self.org).order_by('-start_date')
        term, year, month, period_label = self._resolve_period(request)

        if not period_label:
            today = date.today()
            year = today.year
            month = today.month + 1 if today.month < 12 else 1
            if month == 1:
                year += 1
            period_label = date(year, month, 1).strftime('%B %Y')

        rows = self._member_rows(policies, term, year, month, period_label) if (term or year) else []
        return render(request, self.template_name, {
            'org': self.org,
            'org_membership': self.org_membership,
            'rows': rows,
            'policies': policies,
            'terms': terms,
            'period': period_label,
            'selected_term': term,
            'selected_year': year,
            'selected_month': month,
            'due_date': self._default_due_date(),
        })

    def post(self, request, org_slug):
        term, year, month, period_label = self._resolve_period(request)
        due_date_raw = request.POST.get('due_date', '').strip()
        send_emails = request.POST.get('send_emails') == '1'
        selected_ids = set(request.POST.getlist('member_ids'))
        amounts = {k[len('amount_'):]: v for k, v in request.POST.items() if k.startswith('amount_')}

        if not period_label or not due_date_raw or not selected_ids:
            messages.error(request, 'Period, due date, and at least one member are required.')
            return redirect('invoice_bulk', org_slug=self.org.slug)

        try:
            due_date = date.fromisoformat(due_date_raw)
        except ValueError:
            messages.error(request, 'Invalid due date.')
            return redirect('invoice_bulk', org_slug=self.org.slug)

        already = set(
            Invoice.objects.filter(organisation=self.org, period=period_label)
            .filter(member_id__in=[int(i) for i in selected_ids])
            .values_list('member_id', flat=True)
        )

        from .calculator import calculate_invoice_amount
        created_invoices = []
        skipped = 0

        for member_id_str in selected_ids:
            try:
                member_id = int(member_id_str)
            except ValueError:
                continue
            if member_id in already:
                skipped += 1
                continue

            try:
                member = Member.objects.get(pk=member_id, organisation=self.org, is_active=True)
            except Member.DoesNotExist:
                continue

            # Use admin-overridden amount if provided, else calculated
            override_key = str(member_id)
            if override_key in amounts:
                try:
                    net = float(amounts[override_key])
                    discount = 0
                except ValueError:
                    continue
            else:
                policy = member.billing_policy
                if not policy:
                    from classes.models import ClassMember
                    enrolment = ClassMember.objects.filter(
                        member=member, assigned_class__billing_policy__isnull=False
                    ).select_related('assigned_class__billing_policy').first()
                    policy = enrolment.assigned_class.billing_policy if enrolment else None

                if policy:
                    calc = calculate_invoice_amount(member, policy, term=term, year=year, month=month)
                    net = float(calc['net'])
                    discount = float(calc['discount'])
                else:
                    net = float(member.monthly_fee or 0)
                    discount = 0

            if net <= 0:
                continue

            inv = Invoice.objects.create(
                organisation=self.org,
                member=member,
                billing_policy=member.billing_policy,
                amount=net,
                discount_amount=discount,
                period=period_label,
                due_date=due_date,
            )
            created_invoices.append(inv)

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


# ── Billing Policies ──────────────────────────────────────────────────────────

class BillingPolicyListView(OrgAdminMixin, View):
    template_name = 'billing/policies.html'

    def get(self, request, org_slug):
        policies = BillingPolicy.objects.filter(organisation=self.org).prefetch_related('discounts')
        terms = OrgTerm.objects.filter(organisation=self.org).order_by('-start_date')
        return render(request, self.template_name, {
            'org': self.org,
            'org_membership': self.org_membership,
            'policies': policies,
            'terms': terms,
        })


class BillingPolicyCreateView(OrgAdminMixin, View):
    def post(self, request, org_slug):
        name = request.POST.get('name', '').strip()
        billing_cycle = request.POST.get('billing_cycle', '').strip()
        amount = request.POST.get('amount', '').strip()
        description = request.POST.get('description', '').strip()

        if not name or not billing_cycle or not amount:
            messages.error(request, 'Name, billing cycle, and amount are required.')
            return redirect('billing_policies', org_slug=self.org.slug)

        try:
            amount_val = float(amount)
        except ValueError:
            messages.error(request, 'Invalid amount.')
            return redirect('billing_policies', org_slug=self.org.slug)

        BillingPolicy.objects.create(
            organisation=self.org,
            name=name,
            billing_cycle=billing_cycle,
            amount=amount_val,
            description=description,
        )
        messages.success(request, f'Policy "{name}" created.')
        return redirect('billing_policies', org_slug=self.org.slug)


class BillingPolicyEditView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        policy = get_object_or_404(BillingPolicy, pk=pk, organisation=self.org)
        policy.name = request.POST.get('name', policy.name).strip()
        policy.billing_cycle = request.POST.get('billing_cycle', policy.billing_cycle).strip()
        policy.description = request.POST.get('description', '').strip()
        try:
            policy.amount = float(request.POST.get('amount', policy.amount))
        except ValueError:
            messages.error(request, 'Invalid amount.')
            return redirect('billing_policies', org_slug=self.org.slug)
        policy.is_active = request.POST.get('is_active') == '1'
        policy.save()
        messages.success(request, f'Policy "{policy.name}" updated.')
        return redirect('billing_policies', org_slug=self.org.slug)


class BillingPolicyDeleteView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        policy = get_object_or_404(BillingPolicy, pk=pk, organisation=self.org)
        name = policy.name
        policy.delete()
        messages.success(request, f'Policy "{name}" deleted.')
        return redirect('billing_policies', org_slug=self.org.slug)


class PolicyDiscountCreateView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        policy = get_object_or_404(BillingPolicy, pk=pk, organisation=self.org)
        name = request.POST.get('name', '').strip()
        discount_type = request.POST.get('discount_type', '').strip()
        value = request.POST.get('value', '').strip()
        auto_apply = request.POST.get('auto_apply') == '1'

        if not name or not discount_type or not value:
            messages.error(request, 'All discount fields are required.')
            return redirect('billing_policies', org_slug=self.org.slug)

        try:
            value_val = float(value)
        except ValueError:
            messages.error(request, 'Invalid discount value.')
            return redirect('billing_policies', org_slug=self.org.slug)

        PolicyDiscount.objects.create(
            policy=policy,
            name=name,
            discount_type=discount_type,
            value=value_val,
            auto_apply=auto_apply,
        )
        messages.success(request, f'Discount "{name}" added to {policy.name}.')
        return redirect('billing_policies', org_slug=self.org.slug)


class PolicyDiscountDeleteView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        discount = get_object_or_404(PolicyDiscount, pk=pk, policy__organisation=self.org)
        name = discount.name
        discount.delete()
        messages.success(request, f'Discount "{name}" removed.')
        return redirect('billing_policies', org_slug=self.org.slug)


# ── Org Terms ─────────────────────────────────────────────────────────────────

class OrgTermCreateView(OrgAdminMixin, View):
    def post(self, request, org_slug):
        name = request.POST.get('name', '').strip()
        start_date_raw = request.POST.get('start_date', '').strip()
        end_date_raw = request.POST.get('end_date', '').strip()

        if not name or not start_date_raw or not end_date_raw:
            messages.error(request, 'All term fields are required.')
            return redirect('billing_policies', org_slug=self.org.slug)

        try:
            start_date = date.fromisoformat(start_date_raw)
            end_date = date.fromisoformat(end_date_raw)
        except ValueError:
            messages.error(request, 'Invalid term dates.')
            return redirect('billing_policies', org_slug=self.org.slug)

        OrgTerm.objects.create(
            organisation=self.org,
            name=name,
            start_date=start_date,
            end_date=end_date,
        )
        messages.success(request, f'Term "{name}" added.')
        return redirect('billing_policies', org_slug=self.org.slug)


class OrgTermDeleteView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        term = get_object_or_404(OrgTerm, pk=pk, organisation=self.org)
        name = term.name
        term.delete()
        messages.success(request, f'Term "{name}" deleted.')
        return redirect('billing_policies', org_slug=self.org.slug)


# ── Member Discounts ──────────────────────────────────────────────────────────

class MemberDiscountAddView(OrgAdminMixin, View):
    def post(self, request, org_slug, member_pk):
        from members.models import Member as MemberModel
        member = get_object_or_404(MemberModel, pk=member_pk, organisation=self.org)
        discount_id = request.POST.get('discount_id')
        discount = get_object_or_404(PolicyDiscount, pk=discount_id, policy__organisation=self.org)
        MemberDiscount.objects.get_or_create(member=member, discount=discount)
        messages.success(request, f'Discount "{discount.name}" applied to {member.name}.')
        return redirect('member_detail', org_slug=self.org.slug, pk=member_pk)


class MemberDiscountRemoveView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        md = get_object_or_404(MemberDiscount, pk=pk, member__organisation=self.org)
        member_pk = md.member_id
        name = md.discount.name
        md.delete()
        messages.success(request, f'Discount "{name}" removed.')
        return redirect('member_detail', org_slug=self.org.slug, pk=member_pk)
