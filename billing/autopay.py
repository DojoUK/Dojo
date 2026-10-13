"""
"Pay automatically": a member saves a card or sets up a Direct Debit once
(through Stripe's own page), and each invoice Dojo makes for them is charged
to it. Dojo decides the amounts; Stripe only collects.

Invoices are marked `due` when they're created and collected by the scheduler
(`run_scheduler`) within a minute. Cards succeed or fail straight away; Direct
Debits take about three working days, and Stripe reports the result to the
club's webhook.
"""
import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from . import payments
from .payments import field

log = logging.getLogger(__name__)


def enabled(org):
    """Whether members of `org` can pay automatically.

    Which methods they can save (card, Bacs Direct Debit, ...) is set in the club's
    Stripe dashboard: current Stripe API versions no longer take a list from us.
    """
    payment_settings = payments.settings_for(org)
    return bool(payment_settings is not None and payment_settings.auto_pay != 'off'
                and payments.stripe_keys(org) is not None and org.has_feature('billing'))


def active_autopay(member):
    from .models import AutoPay
    return AutoPay.objects.filter(member=member, active=True).first()


def _recipient_email(member):
    guardian = member.guardians.filter(email__gt='').first()
    return guardian.email if guardian else (member.email or None)


def _customer_id(member, keys):
    import stripe
    if not member.stripe_customer_id:
        customer = stripe.Customer.create(
            api_key=keys.secret, name=member.name, email=_recipient_email(member),
            metadata={'member_pk': str(member.pk), 'organisation': member.organisation.slug},
        )
        member.stripe_customer_id = customer.id
        member.save(update_fields=['stripe_customer_id'])
    return member.stripe_customer_id


def start_setup(member, return_url, invoice=None):
    """A Stripe page where the member saves a card or sets up a Direct Debit. Returns its URL."""
    import stripe
    org = member.organisation
    keys = payments.stripe_keys(org)
    metadata = {'member_pk': str(member.pk), 'organisation': org.slug, 'purpose': 'autopay'}
    if invoice is not None:
        metadata['invoice_pk'] = str(invoice.pk)
    separator = '&' if '?' in return_url else '?'
    session = stripe.checkout.Session.create(
        api_key=keys.secret,
        mode='setup',
        currency='gbp',
        customer=_customer_id(member, keys),
        success_url=f'{return_url}{separator}autopay_session={{CHECKOUT_SESSION_ID}}',
        cancel_url=return_url,
        metadata=metadata,
        setup_intent_data={'metadata': metadata},
    )
    return session.url


def _label(payment_method):
    kind = field(payment_method, 'type')
    if kind not in ('card', 'bacs_debit'):
        return f"{kind.replace('_', ' ').title()} ending {field(field(payment_method, kind), 'last4', '????')}"
    if kind == 'bacs_debit':
        return f"Direct Debit, account ending {field(field(payment_method, 'bacs_debit'), 'last4', '????')}"
    card = field(payment_method, 'card')
    return f"{field(card, 'brand', 'card').title()} ending {field(card, 'last4', '????')}"


def save_from_setup_intent(org, setup_intent):
    """Store the payment method from a completed Stripe setup. Returns the AutoPay, or None."""
    from members.models import Member

    from .models import AutoPay
    member = Member.objects.filter(pk=field(field(setup_intent, 'metadata'), 'member_pk'), organisation=org).first()
    payment_method = field(setup_intent, 'payment_method')
    if member is None or not payment_method or field(setup_intent, 'status') != 'succeeded':
        return None
    if isinstance(payment_method, str):
        import stripe
        payment_method = stripe.PaymentMethod.retrieve(payment_method, api_key=payments.stripe_keys(org).secret)
    autopay, _ = AutoPay.objects.update_or_create(member=member, defaults={
        'payment_method_id': field(payment_method, 'id'),
        'mandate_id': field(setup_intent, 'mandate', ''),
        'method_type': field(payment_method, 'type'),
        'label': _label(payment_method),
        'active': True,
    })
    # Only invoices made from now on are collected automatically, plus the one the member was paying
    # when they set this up. Older unpaid invoices aren't charged without them asking.
    invoice_pk = field(field(setup_intent, 'metadata'), 'invoice_pk')
    if invoice_pk:
        from .models import Invoice
        Invoice.objects.filter(pk=invoice_pk, member=member, status='unpaid',
                               autopay_status__in=['', 'failed']).update(autopay_status='due')
    return autopay


def finish_setup(org, session_id):
    """Called when the member comes back from Stripe, so it works even before the webhook arrives."""
    import stripe
    keys = payments.stripe_keys(org)
    if keys is None:
        return None
    try:
        session = stripe.checkout.Session.retrieve(session_id, api_key=keys.secret,
                                                   expand=['setup_intent.payment_method'])
    except stripe.error.StripeError:
        return None
    if field(session, 'mode') != 'setup' or not field(session, 'setup_intent'):
        return None
    return save_from_setup_intent(org, field(session, 'setup_intent'))


def turn_off(member):
    from .models import AutoPay, Invoice
    AutoPay.objects.filter(member=member).update(active=False)
    Invoice.objects.filter(member=member, autopay_status='due').update(autopay_status='')


def mark_due(invoice):
    """Called when an invoice is created: queue it for collection if the member pays automatically."""
    if invoice.run_id and invoice.run.status == 'awaiting_review':
        return  # held for an admin to check first; queued when they send it (billing.schedule.release)
    if invoice.status == 'unpaid' and enabled(invoice.organisation) and active_autopay(invoice.member):
        type(invoice).objects.filter(pk=invoice.pk).update(autopay_status='due')


def _record_payment(invoice, amount, payment_intent_id):
    from .models import Invoice, Payment
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if invoice.payments.filter(stripe_payment_id=payment_intent_id).exists():
            return
        Payment.objects.create(invoice=invoice, amount=amount, method=Payment.Method.STRIPE,
                               stripe_payment_id=payment_intent_id, paid_at=timezone.now(),
                               notes='Collected automatically')
        invoice.status = 'paid'
        invoice.autopay_status = 'paid'
        invoice.autopay_error = ''
        invoice.save(update_fields=['status', 'autopay_status', 'autopay_error'])


def _fail(invoice, message):
    from .models import Invoice
    Invoice.objects.filter(pk=invoice.pk).update(autopay_status='failed', autopay_error=message[:255])
    invoice.refresh_from_db()
    from .emails import send_invoice_email
    try:
        send_invoice_email(invoice, autopay_failed=message)
    except Exception:  # the failure is already recorded on the invoice; a mail problem mustn't hide it
        log.exception('Could not email member about failed automatic payment for invoice %s', invoice.pk)


def collect(invoice):
    """Charge the member's saved card or Direct Debit for what's left to pay on `invoice`."""
    import stripe

    from .models import Invoice
    org = invoice.organisation
    keys = payments.stripe_keys(org)
    autopay = active_autopay(invoice.member)
    balance = invoice.balance_due
    if keys is None or autopay is None or balance <= 0 or not enabled(org):
        Invoice.objects.filter(pk=invoice.pk).update(autopay_status='')
        return
    attempt = invoice.autopay_attempts + 1
    Invoice.objects.filter(pk=invoice.pk).update(autopay_attempts=attempt)
    params = {
        'api_key': keys.secret,
        'amount': int((balance * 100).quantize(Decimal(1))),
        'currency': 'gbp',
        'customer': invoice.member.stripe_customer_id,
        'payment_method': autopay.payment_method_id,
        # Charge the saved method as it is, with no redirects: the member isn't there to follow one.
        'automatic_payment_methods': {'enabled': True, 'allow_redirects': 'never'},
        'off_session': True,
        'confirm': True,
        'description': f'{org.name}: {invoice.period} ({invoice.payment_reference})',
        'metadata': {'invoice_pk': str(invoice.pk), 'organisation': org.slug, 'purpose': 'autopay'},
        # The same attempt can never charge twice, even if collection is retried.
        'idempotency_key': f'dojo-{org.slug}-invoice-{invoice.pk}-attempt-{attempt}',
    }
    if autopay.mandate_id:
        params['mandate'] = autopay.mandate_id
    try:
        intent = stripe.PaymentIntent.create(**params)
    except stripe.error.CardError as error:
        _fail(invoice, error.user_message or 'The card was declined.')
        return
    except stripe.error.StripeError as error:
        _fail(invoice, f'Stripe couldn\'t take the payment: {error.user_message or error}')
        return
    intent_id, status = field(intent, 'id'), field(intent, 'status')
    Invoice.objects.filter(pk=invoice.pk).update(autopay_payment_intent=intent_id)
    if status == 'succeeded':
        _record_payment(invoice, Decimal(field(intent, 'amount_received', 0)) / 100, intent_id)
    elif status == 'processing':
        Invoice.objects.filter(pk=invoice.pk).update(autopay_status='processing')
    else:
        _fail(invoice, 'Your bank asked for the payment to be confirmed, so it couldn\'t be taken automatically.')


def collect_due(limit=50):
    """Collect invoices waiting for automatic payment. Each one is claimed first, so two schedulers can't both charge it."""
    from .models import Invoice
    collected = 0
    for pk in Invoice.objects.filter(autopay_status='due', status='unpaid').values_list('pk', flat=True)[:limit]:
        if Invoice.objects.filter(pk=pk, autopay_status='due').update(autopay_status='collecting') != 1:
            continue
        invoice = Invoice.objects.select_related('organisation', 'member').get(pk=pk)
        try:
            collect(invoice)
        except Exception:
            log.exception('Automatic payment for invoice %s failed unexpectedly', pk)
            Invoice.objects.filter(pk=pk, autopay_status='collecting').update(
                autopay_status='failed', autopay_error='Something went wrong collecting this payment.')
        collected += 1
    return collected


def payment_intent_succeeded(org, intent):
    """Webhook: a Direct Debit (or delayed card) payment has cleared."""
    from .models import Invoice
    metadata = field(intent, 'metadata')
    if field(metadata, 'purpose') != 'autopay':
        return  # one-off card payments are recorded from checkout.session.completed
    invoice = Invoice.objects.filter(pk=field(metadata, 'invoice_pk'), organisation=org).first()
    if invoice is not None:
        _record_payment(invoice, Decimal(field(intent, 'amount_received', 0)) / 100, field(intent, 'id'))


def payment_intent_failed(org, intent):
    """Webhook: a Direct Debit (or card) payment didn't go through."""
    from .models import Invoice
    metadata = field(intent, 'metadata')
    if field(metadata, 'purpose') != 'autopay':
        return
    # Card declines are handled when the charge is made; this is for Direct Debits still in progress.
    invoice = Invoice.objects.filter(pk=field(metadata, 'invoice_pk'), organisation=org, status='unpaid',
                                     autopay_status='processing').first()
    if invoice is not None:
        error = field(field(intent, 'last_payment_error'), 'message', 'The payment didn\'t go through.')
        _fail(invoice, error)
