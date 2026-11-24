"""
"Pay automatically": a member saves a card or sets up a Direct Debit once
(through Stripe's own page), and each invoice Dojo makes for them is charged
to it. Dojo decides the amounts; Stripe only collects.

Invoices are marked `due` when they're created and collected by the scheduler
(`run_scheduler`) within a minute. Cards succeed or fail straight away; Direct
Debits take about three working days, and Stripe reports the result to the
club's webhook.

Cards expire, so Dojo keeps each saved card's expiry date. Stripe's card updater
reports cards that banks have replaced (payment_method.automatically_updated),
and warn_expiring_cards() emails members whose card is about to run out.
"""
import calendar
import logging
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F
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


def _expiry(payment_method):
    """The last day a card works (the end of its expiry month), or None for anything else."""
    if field(payment_method, 'type') != 'card':
        return None
    card = field(payment_method, 'card')
    try:
        year, month = int(field(card, 'exp_year')), int(field(card, 'exp_month'))
        return date(year, month, calendar.monthrange(year, month)[1])
    except (TypeError, ValueError):
        return None


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
        'expires_on': _expiry(payment_method),
        'expiry_warned_for': None,
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


def payment_method_updated(org, payment_method):
    """Webhook: Stripe's card updater has new details for a card (a bank sent a replacement), or the
    card was edited. The saved card keeps working, so just record its new label and expiry."""
    from .models import AutoPay
    AutoPay.objects.filter(member__organisation=org, payment_method_id=field(payment_method, 'id')).update(
        label=_label(payment_method), expires_on=_expiry(payment_method), expiry_warned_for=None)


# ── Expiring cards ────────────────────────────────────────────────────────────

WARN_FROM_HOUR = 9


def _fill_in_expiry(autopay, keys):
    """Fetch the expiry of a card saved before Dojo kept it. Returns True if it was found."""
    import stripe

    from .models import AutoPay
    try:
        payment_method = stripe.PaymentMethod.retrieve(autopay.payment_method_id, api_key=keys.secret)
    except stripe.error.StripeError:
        return False
    autopay.expires_on = _expiry(payment_method)
    AutoPay.objects.filter(pk=autopay.pk).update(expires_on=autopay.expires_on)
    return autopay.expires_on is not None


def _expiry_text(autopay, today):
    month = f'{autopay.expires_on:%B %Y}'
    if autopay.expires_on < today:
        return f'expired at the end of {month}', 'has expired'
    return f'expires at the end of {month}', 'expires soon'


def warn_expiring_cards(now=None, fill_in_limit=25):
    """
    Email each member whose saved card expires within AutoPay.EXPIRY_WARNING_DAYS (or has expired),
    once per card, with a link to update it. Cards saved before Dojo kept expiry dates are looked up
    in Stripe a few at a time. Returns how many members were emailed.
    """
    from django.conf import settings
    from django.urls import reverse

    from mailer import compose

    from .models import AutoPay
    now = timezone.localtime(now or timezone.now())
    if now.hour < WARN_FROM_HOUR:
        return 0
    today = now.date()
    cards = (AutoPay.objects.filter(active=True, method_type='card', member__is_active=True)
             .select_related('member__organisation'))
    for autopay in cards.filter(expires_on__isnull=True)[:fill_in_limit]:
        keys = payments.stripe_keys(autopay.member.organisation)
        if keys is not None:
            _fill_in_expiry(autopay, keys)

    warned = 0
    due = cards.filter(expires_on__lte=today + timedelta(days=AutoPay.EXPIRY_WARNING_DAYS)).exclude(
        expiry_warned_for=F('expires_on'))
    for autopay in due:
        member, org = autopay.member, autopay.member.organisation
        recipient = _recipient_email(member)
        if not recipient or not enabled(org):
            continue
        # Claim it first, so two schedulers can't both email the same member.
        if not AutoPay.objects.filter(pk=autopay.pk).exclude(expiry_warned_for=autopay.expires_on).update(
                expiry_warned_for=autopay.expires_on):
            continue
        expiry, expiry_short = _expiry_text(autopay, today)
        link = settings.SITE_URL.rstrip('/') + reverse('member_portal', kwargs={'token': member.token}) + '#paying'
        try:
            compose.send(org, 'card_expiring', [recipient],
                         {**compose.member_values(member, to_guardian=recipient != member.email),
                          'card': autopay.label, 'expiry': expiry, 'expiry_short': expiry_short,
                          'update_link': link})
        except Exception:
            log.exception('Could not email member %s about their expiring card', member.pk)
            AutoPay.objects.filter(pk=autopay.pk).update(expiry_warned_for=None)   # try again next run
            continue
        warned += 1
    return warned


def needing_attention(org, today=None):
    """(failed invoices, cards expiring or expired) for the club's dashboard."""
    from .models import AutoPay, Invoice
    today = today or timezone.localdate()
    failed = (Invoice.objects.filter(organisation=org, status='unpaid', autopay_status='failed')
              .select_related('member').order_by('-due_date'))
    expiring = (AutoPay.objects.filter(member__organisation=org, member__is_active=True, active=True,
                                       expires_on__lte=today + timedelta(days=AutoPay.EXPIRY_WARNING_DAYS))
                .select_related('member').order_by('expires_on'))
    return failed, expiring
