"""
How a club's members pay: bank transfer details, card payments through Stripe,
and the "Pay now" link sent with every invoice.
"""
from dataclasses import dataclass

from django.conf import settings
from django.core import signing
from django.urls import reverse

PAY_LINK_SALT = 'dojo.billing.pay-link'


@dataclass(frozen=True)
class StripeKeys:
    publishable: str
    secret: str
    webhook_secret: str


def field(obj, key, default=None):
    """obj[key] for a dict or a Stripe object (which has no .get()), or `default` if missing or empty."""
    if obj is None:
        return default
    try:
        value = obj[key]
    except (KeyError, TypeError, AttributeError):
        return default
    return default if value is None else value


def settings_for(org):
    """The club's PaymentSettings, or None if it hasn't set any (older clubs)."""
    from .models import PaymentSettings
    return PaymentSettings.objects.filter(organisation=org).first()


def stripe_keys(org):
    """The Stripe keys to use for `org`, or None if card payments aren't set up.

    Keys from Settings → Payments win; clubs that haven't set any fall back to
    the STRIPE_* environment variables (how card payments were set up before).
    """
    payment_settings = settings_for(org)
    if payment_settings is not None:
        if payment_settings.accept_card and payment_settings.stripe_secret_key:
            return StripeKeys(payment_settings.stripe_publishable_key, payment_settings.stripe_secret_key,
                              payment_settings.stripe_webhook_secret)
        return None
    if settings.STRIPE_PUBLIC_KEY and settings.STRIPE_SECRET_KEY:
        return StripeKeys(settings.STRIPE_PUBLIC_KEY, settings.STRIPE_SECRET_KEY, settings.STRIPE_WEBHOOK_SECRET)
    return None


def pay_token(invoice):
    return signing.dumps(invoice.pk, salt=PAY_LINK_SALT)


def invoice_from_token(token):
    """The invoice a pay link is for, or None if the link isn't genuine."""
    from .models import Invoice
    try:
        pk = signing.loads(token, salt=PAY_LINK_SALT)
    except signing.BadSignature:
        return None
    return Invoice.objects.select_related('organisation', 'member').filter(pk=pk).first()


def pay_url(invoice):
    return settings.SITE_URL.rstrip('/') + reverse('invoice_pay', kwargs={'token': pay_token(invoice)})


def options_for(invoice):
    """Ways to pay this invoice, for the pay page and invoice emails."""
    org = invoice.organisation
    payment_settings = settings_for(org)
    bank = None
    if payment_settings and payment_settings.accept_bank_transfer and payment_settings.bank_account_number:
        bank = {
            'account_name': payment_settings.bank_account_name or org.name,
            'sort_code': payment_settings.bank_sort_code,
            'account_number': payment_settings.bank_account_number,
        }
    return {
        'pay_url': pay_url(invoice),
        'payment_reference': invoice.payment_reference,
        'card_payments': stripe_keys(org) is not None and org.has_feature('billing'),
        'bank_transfer': bank,
        'cash_payments': bool(payment_settings and payment_settings.accept_cash),
        'payment_instructions': payment_settings.instructions if payment_settings else '',
    }
