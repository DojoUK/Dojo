"""
The page a member reaches from "Pay now" in an invoice email. It needs no
login: the link itself is signed, so it can't be guessed or changed.
"""
import logging
from decimal import Decimal

from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from . import payments

log = logging.getLogger(__name__)


def _invoice_or_404(token):
    invoice = payments.invoice_from_token(token)
    if invoice is None:
        raise Http404('Payment link not recognised.')
    return invoice


def _recipient_email(member):
    guardian = member.guardians.filter(email__gt='').first()
    return guardian.email if guardian else (member.email or None)


class InvoicePayView(View):
    def get(self, request, token):
        invoice = _invoice_or_404(token)
        from . import autopay
        just_set_up = None
        if request.GET.get('autopay_session'):
            just_set_up = autopay.finish_setup(invoice.organisation, request.GET['autopay_session'])
            if just_set_up is not None and invoice.balance_due > 0:
                # Take this invoice straight away rather than waiting for the scheduler.
                invoice.refresh_from_db()
                if type(invoice).objects.filter(pk=invoice.pk, autopay_status='due').update(autopay_status='collecting'):
                    autopay.collect(invoice)
                invoice.refresh_from_db()
        payment_settings = payments.settings_for(invoice.organisation)
        return render(request, 'billing/pay.html', {
            'autopay_methods': autopay.methods_for(invoice.organisation),
            'autopay': autopay.active_autopay(invoice.member),
            'autopay_expected': bool(payment_settings and payment_settings.auto_pay == 'encouraged'),
            'autopay_just_set_up': just_set_up,
            'invoice': invoice,
            'org': invoice.organisation,
            'first_name': invoice.member.name.split(' ')[0],
            'balance_due': invoice.balance_due,
            'just_paid': request.GET.get('paid') == '1',
            'payment_error': request.GET.get('payment_error') == '1',
            'token': token,
            **payments.options_for(invoice),
        })


class InvoiceCardPaymentView(View):
    """Starts a Stripe Checkout payment for what's left to pay on the invoice."""

    def post(self, request, token):
        import stripe
        invoice = _invoice_or_404(token)
        org = invoice.organisation
        keys = payments.stripe_keys(org)
        page = request.build_absolute_uri(reverse('invoice_pay', kwargs={'token': token}))
        balance = invoice.balance_due
        if keys is None or balance <= 0:
            return redirect(page)
        try:
            session = self._checkout(stripe, keys, invoice, org, balance, page)
        except stripe.error.StripeError:
            log.exception('Stripe refused a card payment for invoice %s', invoice.pk)
            return redirect(page + '?payment_error=1')
        return redirect(session.url, permanent=False)

    def _checkout(self, stripe, keys, invoice, org, balance, page):
        return stripe.checkout.Session.create(
            api_key=keys.secret,
            mode='payment',
            line_items=[{
                'price_data': {
                    'currency': 'gbp',
                    'unit_amount': int((balance * 100).quantize(Decimal(1))),
                    'product_data': {
                        'name': f'{org.name}: {invoice.period}',
                        'description': f'{invoice.member.name} · reference {invoice.payment_reference}',
                    },
                },
                'quantity': 1,
            }],
            success_url=page + '?paid=1',
            cancel_url=page,
            customer_email=_recipient_email(invoice.member),
            client_reference_id=invoice.payment_reference,
            metadata={'invoice_pk': str(invoice.pk), 'organisation': org.slug},
            payment_intent_data={'metadata': {'invoice_pk': str(invoice.pk), 'organisation': org.slug}},
        )


class InvoiceAutoPaySetupView(View):
    """Sends the member to Stripe to save a card or set up a Direct Debit; this invoice is then taken from it."""

    def post(self, request, token):
        from . import autopay
        invoice = _invoice_or_404(token)
        page = request.build_absolute_uri(reverse('invoice_pay', kwargs={'token': token}))
        if not autopay.methods_for(invoice.organisation):
            return redirect(page)
        import stripe
        try:
            return redirect(autopay.start_setup(invoice.member, page, invoice=invoice))
        except stripe.error.StripeError:
            log.exception('Stripe refused to start automatic payment setup for member %s', invoice.member_id)
            return redirect(page + '?payment_error=1')
