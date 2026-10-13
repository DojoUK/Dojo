"""
The page a member reaches from "Pay now" in an invoice email. It needs no
login: the link itself is signed, so it can't be guessed or changed.
"""

from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from . import payments


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
        return render(request, 'billing/pay.html', {
            'invoice': invoice,
            'org': invoice.organisation,
            'first_name': invoice.member.name.split(' ')[0],
            'balance_due': invoice.balance_due,
            'just_paid': request.GET.get('paid') == '1',
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
        session = stripe.checkout.Session.create(
            api_key=keys.secret,
            mode='payment',
            line_items=payments.checkout_line_items(invoice),
            custom_text={'submit': {'message': payments.checkout_note(invoice)}},
            success_url=page + '?paid=1',
            cancel_url=page,
            customer_email=_recipient_email(invoice.member),
            client_reference_id=invoice.payment_reference,
            metadata={'invoice_pk': str(invoice.pk), 'organisation': org.slug},
            payment_intent_data={'metadata': {'invoice_pk': str(invoice.pk), 'organisation': org.slug}},
        )
        return redirect(session.url, permanent=False)
