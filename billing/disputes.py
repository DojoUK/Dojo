"""
Chargebacks: when a member asks their bank to reverse a Stripe payment.

Stripe sends charge.dispute.created when it happens, charge.dispute.updated as it moves along, and
charge.dispute.closed when the bank decides. Dojo keeps a PaymentDispute for each, tells the club's
admins straight away (they have a deadline to send evidence, on Stripe's dashboard), shows open ones
on the dashboard, and if the club loses, puts the money back on the invoice as due again.
"""
import logging
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .payments import field

log = logging.getLogger(__name__)

CLOSED = ('won', 'lost', 'warning_closed', 'prevented')


def record_dispute(org, dispute):
    """Create or update the PaymentDispute for a Stripe dispute object. Returns it, or None if it isn't ours."""
    from .models import Payment, PaymentDispute

    dispute_id = field(dispute, 'id')
    if not dispute_id:
        return None
    payment_intent = field(dispute, 'payment_intent') or ''
    payment = None
    if payment_intent:
        payments = Payment.objects.filter(stripe_payment_id=payment_intent, amount__gt=0).select_related('invoice')
        if org is not None:
            payments = payments.filter(invoice__organisation=org)
        payment = payments.first()
    if org is None:   # the installation-wide webhook: the club is whoever was paid
        org = payment.invoice.organisation if payment else None
    if org is None:
        return None

    due_by = field(field(dispute, 'evidence_details'), 'due_by')
    values = {
        'payment_intent': payment_intent,
        'amount': Decimal(field(dispute, 'amount', 0) or 0) / 100,
        'reason': field(dispute, 'reason', '') or '',
        'status': field(dispute, 'status', '') or '',
        'evidence_due_by': datetime.fromtimestamp(due_by, tz=dt_timezone.utc) if due_by else None,
        'livemode': bool(field(dispute, 'livemode', False)),
    }
    with transaction.atomic():
        record, created = PaymentDispute.objects.select_for_update().get_or_create(
            stripe_dispute_id=dispute_id,
            defaults={**values, 'organisation': org, 'payment': payment,
                      'invoice': payment.invoice if payment else None},
        )
        if record.organisation_id != org.pk:
            return None
        for name, value in values.items():
            setattr(record, name, value)
        if payment and not record.payment_id:
            record.payment, record.invoice = payment, payment.invoice
        if record.status in CLOSED and record.closed_at is None:
            record.closed_at = timezone.now()
        record.save()
        if record.status == 'lost':
            _money_returned(record)
    if created:
        tell_admins(record)
    return record


def _money_returned(record):
    """A lost dispute: the bank gave the member their money back, so the invoice is due again."""
    from .models import Invoice, Payment

    if record.chargeback_payment_id or record.invoice_id is None:
        return
    invoice = record.invoice
    record.chargeback_payment = Payment.objects.create(
        invoice=invoice, method=Payment.Method.STRIPE, stripe_payment_id=record.stripe_dispute_id,
        amount=-record.amount, paid_at=timezone.now(),
        notes=f"Chargeback lost: £{record.amount:.2f} went back to the member's bank",
    )
    record.save(update_fields=['chargeback_payment'])
    if invoice.status == Invoice.Status.PAID and invoice.amount_paid < invoice.amount:
        invoice.status = Invoice.Status.UNPAID
        # Not collected again automatically: the member disputed it, so the club decides what happens next.
        invoice.autopay_status = ''
        invoice.save(update_fields=['status', 'autopay_status'])


def open_disputes(org):
    from .models import PaymentDispute
    return (PaymentDispute.objects.filter(organisation=org, status__in=PaymentDispute.OPEN)
            .select_related('invoice__member').order_by('evidence_due_by', 'opened_at'))


def tell_admins(record):
    """Email the club's admins about a new dispute: they have a deadline to respond."""
    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives
    from django.urls import reverse

    from mailer import connection_for
    from organisations.models import OrganisationMember

    org = record.organisation
    recipients = list(OrganisationMember.objects.filter(organisation=org, role='org_admin')
                      .exclude(user__email='').values_list('user__email', flat=True))
    if not recipients:
        return
    member = record.member
    who = (member.register_name or member.name) if member else 'A member'
    due_at = timezone.localtime(record.evidence_due_by) if record.evidence_due_by else None
    due = f'{due_at.day} {due_at:%B %Y}' if due_at else ''
    if record.is_enquiry:
        subject = f'A bank is asking about a £{record.amount:.2f} payment'
        opening = (f"{who}'s bank has asked about a £{record.amount:.2f} payment ({record.reason_text.lower()}). "
                   f"This is an enquiry, not yet a chargeback: no money has been taken back.")
    else:
        subject = f'Payment disputed: {who}, £{record.amount:.2f}'
        opening = (f'{who} has disputed a £{record.amount:.2f} payment with their bank '
                   f'({record.reason_text.lower()}). Stripe has taken the £{record.amount:.2f}, and a dispute fee, '
                   f'from your balance while the bank decides.')
    lines = [opening, '']
    if record.needs_response:
        lines += [f"Respond{' by ' + due if due else ''} on Stripe with evidence, such as the invoice, attendance "
                  f"and your membership terms. If you don't respond, the bank usually sides with the member.", '']
    lines.append(f'Respond on Stripe: {record.stripe_url}')
    if record.invoice_id:
        lines.append('The invoice in Dojo: ' + settings.SITE_URL.rstrip('/')
                     + reverse('invoice_detail', kwargs={'org_slug': org.slug, 'pk': record.invoice_id}))
    lines += ['', 'Powered by Dojo']
    try:
        EmailMultiAlternatives(subject, '\n'.join(lines), to=recipients, connection=connection_for(org)).send()
    except Exception:  # the dispute is on the dashboard either way
        log.exception('Could not tell admins about dispute %s', record.stripe_dispute_id)
