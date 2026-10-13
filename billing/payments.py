"""
How a club's members pay: bank transfer details, card payments through Stripe,
and the "Pay now" link sent with every invoice.
"""
from dataclasses import dataclass
from decimal import Decimal

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


def _pence(amount):
    return int((Decimal(amount) * 100).quantize(Decimal(1)))


def _line(name, amount, description, quantity=1, image=''):
    product = {'name': name[:250], 'description': description[:500]}
    if image:
        product['images'] = [image]
    return {'price_data': {'currency': 'gbp', 'unit_amount': _pence(amount), 'product_data': product},
            'quantity': quantity}


def checkout_line_items(invoice):
    """What the member sees on Stripe's payment page, itemised. The lines always add up to the balance due."""
    from organisations.branding import absolute_logo_url
    org = invoice.organisation
    balance = invoice.balance_due
    amount = Decimal(invoice.amount)
    reference = invoice.payment_reference
    for_whom = f'For {invoice.member.name} · reference {reference}'
    # Stripe fetches product images itself, so only offer one it can reach.
    logo = absolute_logo_url(org)
    image = logo if logo.startswith('https://') else ''

    items = [item for item in invoice.items.all() if item.line_total > 0]
    fee = amount - sum((item.line_total for item in items), Decimal(0))
    lines = []
    if balance == amount and fee >= 0:
        if fee > 0:
            plan = invoice.billing_policy.name if invoice.billing_policy else ''
            description = for_whom
            if invoice.discount_amount:
                description += f' · includes a £{Decimal(invoice.discount_amount):.2f} discount'
            lines.append(_line(f'{plan}: {invoice.period}' if plan else invoice.period, fee, description, image=image))
        for item in items:
            lines.append(_line(item.description or str(item.variant), item.unit_price,
                               f'{invoice.period} · reference {reference}', quantity=item.quantity, image=image))
    if not lines or sum(line['price_data']['unit_amount'] * line['quantity'] for line in lines) != _pence(balance):
        # Part-paid (or unusual) invoices: one line for what's left, saying what's been paid already.
        paid = invoice.amount_paid
        description = for_whom
        if paid:
            description = f'£{paid:.2f} of £{amount:.2f} already paid. ' + description
        lines = [_line(f'{invoice.period}: remaining balance' if paid else invoice.period, balance, description,
                       image=image)]
    return lines


def checkout_note(invoice):
    """A line of text Stripe shows above the Pay button."""
    org = invoice.organisation
    first_name = invoice.member.name.split(' ')[0]
    note = f'You\'re paying {org.name} for {first_name}: {invoice.period} (reference {invoice.payment_reference}).'
    if org.email:
        note += f' Questions? Email {org.email}.'
    return note
