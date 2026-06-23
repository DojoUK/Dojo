from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.urls import reverse


def send_welcome_email(member):
    """Send a welcome email to the member (or their guardian if no member email). Returns (success, recipient_or_error)."""
    recipient = member.email
    has_guardians = member.guardians.exists()
    if not recipient:
        guardian = member.guardians.filter(email__gt='').first()
        if guardian:
            recipient = guardian.email
    if not recipient:
        return False, 'No email address on file.'

    org_name = member.organisation.name
    subject = f'Welcome to {org_name}'

    portal_path = reverse('member_portal', kwargs={'token': member.token})
    site_url = getattr(settings, 'SITE_URL', 'http://localhost:8000')
    portal_url = f"{site_url.rstrip('/')}{portal_path}"

    context = {
        'member': member,
        'org_name': org_name,
        'portal_url': portal_url,
        'has_guardians': has_guardians,
    }
    html_body = render_to_string('emails/welcome.html', context)
    greeting = f"Dear guardian of {member.name}" if has_guardians and not member.email else f"Hi {member.name}"
    text_body = (
        f"{greeting},\n\n"
        f"Welcome to {org_name}! We're glad to have you with us.\n\n"
        f"You can view your membership details, check invoices, and pay online here:\n"
        f"{portal_url}\n\n"
        f"If you have any questions, please get in touch.\n\n"
        f"See you on the mat,\n{org_name}\n"
    )

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    msg.attach_alternative(html_body, 'text/html')
    msg.send()
    return True, recipient
