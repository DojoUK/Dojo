from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.urls import reverse
from django.utils import timezone


def send_invoice_email(invoice, request=None):
    member = invoice.member
    has_guardians = member.guardians.exists()

    if has_guardians:
        guardian = member.guardians.filter(email__gt='').first()
        recipient = guardian.email if guardian else None
    else:
        recipient = member.email or None

    if not recipient:
        return False, 'No email address on file for this member or their guardian.'

    portal_path = reverse('member_portal', kwargs={'token': member.token})
    if request:
        portal_url = request.build_absolute_uri(portal_path)
    else:
        site_url = getattr(settings, 'SITE_URL', 'http://localhost:8000')
        portal_url = f"{site_url.rstrip('/')}{portal_path}"

    org_name = invoice.organisation.name
    subject = f'Invoice from {org_name} — {invoice.period}'

    context = {
        'invoice': invoice,
        'org_name': org_name,
        'member': member,
        'portal_url': portal_url,
        'has_guardians': has_guardians,
    }
    html_body = render_to_string('emails/invoice.html', context)
    text_body = (
        f"{'Hi' if not has_guardians else 'Dear guardian of'} {member.name},\n\n"
        f"Invoice from {org_name} for {invoice.period}.\n\n"
        f"Amount due: £{invoice.amount:.2f}\n"
        f"Due date: {invoice.due_date.strftime('%d %b %Y')}\n"
    )
    if invoice.notes:
        text_body += f"\n{invoice.notes}\n"
    text_body += f"\nView and pay your invoice: {portal_url}\n"

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    msg.attach_alternative(html_body, 'text/html')
    msg.send()
    return True, recipient


def send_reminder_email(invoice, request=None):
    member = invoice.member
    has_guardians = member.guardians.exists()

    if has_guardians:
        guardian = member.guardians.filter(email__gt='').first()
        recipient = guardian.email if guardian else None
    else:
        recipient = member.email or None

    if not recipient:
        return False, 'No email address on file.'

    portal_path = reverse('member_portal', kwargs={'token': member.token})
    if request:
        portal_url = request.build_absolute_uri(portal_path)
    else:
        site_url = getattr(settings, 'SITE_URL', 'http://localhost:8000')
        portal_url = f"{site_url.rstrip('/')}{portal_path}"

    org = invoice.organisation
    context = {
        'invoice': invoice,
        'org_name': org.name,
        'org_email': org.email or settings.DEFAULT_FROM_EMAIL,
        'member': member,
        'portal_url': portal_url,
        'has_guardians': has_guardians,
    }
    html_body = render_to_string('emails/invoice_reminder.html', context)
    subject = f"Payment reminder — {invoice.period} ({org.name})"
    text_body = (
        f"This is a reminder that your invoice for {invoice.period} "
        f"(£{invoice.amount:.2f}, due {invoice.due_date.strftime('%d %b %Y')}) is overdue.\n\n"
        f"Pay here: {portal_url}\n"
    )
    msg = EmailMultiAlternatives(
        subject=subject, body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL, to=[recipient],
    )
    msg.attach_alternative(html_body, 'text/html')
    msg.send()

    invoice.reminder_sent_at = timezone.now()
    invoice.save(update_fields=['reminder_sent_at'])
    return True, recipient
