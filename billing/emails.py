from django.conf import settings
from django.urls import reverse
from django.utils import timezone


def portal_url_for(member, request=None):
    """Absolute link to the member's portal, or None when the club has the portal switched off."""
    if not member.organisation.has_feature('member_portal'):
        return None
    portal_path = reverse('member_portal', kwargs={'token': member.token})
    if request:
        return request.build_absolute_uri(portal_path)
    site_url = getattr(settings, 'SITE_URL', 'http://localhost:8000')
    return f"{site_url.rstrip('/')}{portal_path}"


def _autopay_label(invoice):
    """The saved card or Direct Debit this invoice will be collected from, if any."""
    if invoice.autopay_status not in ('due', 'collecting', 'processing'):
        return ''
    from .autopay import active_autopay
    autopay = active_autopay(invoice.member)
    return autopay.label if autopay else ''


def _recipient(member):
    """Invoices go to a guardian when the member has one (with an address), otherwise to the member."""
    if member.guardians.exists():
        guardian = member.guardians.filter(email__gt='').first()
        return (guardian.email if guardian else None), True
    return (member.email or None), False


def send_invoice_email(invoice, request=None, autopay_failed=''):
    return _send_invoice(invoice, 'invoice', autopay_failed=autopay_failed)


def send_reminder_email(invoice, request=None, recipient=None, to_guardian=False):
    sent = _send_invoice(invoice, 'invoice_reminder', overdue=True, recipient=recipient, to_guardian=to_guardian)
    if sent[0]:
        invoice.reminder_sent_at = timezone.now()
        invoice.save(update_fields=['reminder_sent_at'])
    return sent


def _send_invoice(invoice, key, overdue=False, autopay_failed='', recipient=None, to_guardian=False):
    """Send one of the invoice emails. Returns (success, recipient_or_error)."""
    from mailer import blocks, compose
    if recipient is None:
        recipient, to_guardian = _recipient(invoice.member)
    if not recipient:
        return False, 'No email address on file.'
    values = {**compose.member_values(invoice.member, to_guardian), **blocks.invoice_values(invoice)}
    email_blocks = blocks.invoice_blocks(invoice, values, overdue=overdue, autopay_failed=autopay_failed,
                                         autopay_label='' if autopay_failed else _autopay_label(invoice))
    compose.send(invoice.organisation, key, [recipient], values, email_blocks)
    return True, recipient
