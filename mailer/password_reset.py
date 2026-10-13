"""Password reset emails, sent with the club's name, colours and (customisable) wording."""
from django.conf import settings
from django.contrib.auth.forms import PasswordResetForm
from django.urls import reverse

from . import blocks, compose


def club_for(user):
    """The club a person's emails come from: their first club, or the only club on the server."""
    membership = user.organisation_memberships.select_related('organisation').first()
    if membership:
        return membership.organisation
    from organisations.models import Organisation
    orgs = list(Organisation.objects.all()[:2])
    return orgs[0] if len(orgs) == 1 else None


def _expiry():
    days = settings.PASSWORD_RESET_TIMEOUT // 86400
    if days >= 1:
        return f'{days} day{"s" if days != 1 else ""}'
    hours = max(1, settings.PASSWORD_RESET_TIMEOUT // 3600)
    return f'{hours} hour{"s" if hours != 1 else ""}'


class ClubPasswordResetForm(PasswordResetForm):
    """Django's reset form, but the email is the club's "Password reset" email."""

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email,
                  html_email_template_name=None):
        user = context['user']
        org = context.get('organisation') or club_for(user)
        if org is None:  # a superuser on a server with several clubs and none of their own
            return super().send_mail(subject_template_name, email_template_name, context, from_email, to_email,
                                     html_email_template_name)
        path = reverse('password_reset_confirm', kwargs={'uidb64': context['uid'], 'token': context['token']})
        reset_link = f"{context['protocol']}://{context['domain']}{path}"
        values = {
            'first_name': user.first_name or user.username,
            'username': user.get_username(),
            'reset_link': reset_link,
            'link_expires': _expiry(),
        }
        compose.send(org, 'password_reset', [to_email], values, {'reset_button': blocks.reset_button(org, reset_link)})
