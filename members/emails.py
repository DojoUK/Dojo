from django.conf import settings
from django.urls import reverse

from mailer import blocks, compose


def _recipient(member):
    if member.email:
        return member.email
    guardian = member.guardians.filter(email__gt='').first()
    return guardian.email if guardian else None


def _portal_link(member):
    site_url = getattr(settings, 'SITE_URL', 'http://localhost:8000')
    return f"{site_url.rstrip('/')}{reverse('member_portal', kwargs={'token': member.token})}"


def _send_portal_email(member, key):
    recipient = _recipient(member)
    if not recipient:
        return False, 'No email address on file.'
    org = member.organisation
    portal_link = _portal_link(member)
    compose.send(org, key, [recipient], {**compose.member_values(member), 'portal_link': portal_link},
                 {'portal_button': blocks.portal_button(org, portal_link)})
    return True, recipient


def send_welcome_email(member):
    """Send a welcome email to the member (or their guardian if no member email). Returns (success, recipient_or_error)."""
    return _send_portal_email(member, 'welcome')


def send_portal_link_refreshed_email(member):
    """Tell a member their portal token was auto-rotated and share the new link. Returns (success, recipient_or_error)."""
    return _send_portal_email(member, 'portal_link_refreshed')
