"""Member accounts: creating them, verifying the email address, and linking club memberships to them."""
from django.conf import settings
from django.contrib.auth.models import User
from django.core import signing
from django.core.mail import send_mail
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import MemberAccount

VERIFY_SALT = 'network.verify-email'
VERIFY_DAYS = 7


def normalise_email(email):
    return (email or '').strip().lower()


def email_taken(email):
    email = normalise_email(email)
    return User.objects.filter(Q(username__iexact=email) | Q(email__iexact=email)).exists()


def create_account(name, email, password, first_name='', surname=''):
    """A new account. Give the name as first name and surname, or as one full name to be split."""
    email = normalise_email(email)
    account = MemberAccount(name=name, first_name=first_name, surname=surname)
    account.sync_names()
    user = User.objects.create_user(username=email, email=email, password=password,
                                    first_name=account.first_name[:150], last_name=account.surname[:150])
    account.user = user
    account.save()
    return account


def account_for(user):
    """The member account for a signed-in user. Club staff get one made from their staff sign-in the
    first time they use the member side, so one sign-in covers both."""
    if not user.is_authenticated:
        return None
    account = getattr(user, 'member_account', None)
    if account is None:
        account = MemberAccount.objects.create(user=user, name=user.get_full_name() or user.username)
    return account


def verify_token(account):
    return signing.dumps({'a': account.pk, 'e': account.email}, salt=VERIFY_SALT)


def account_from_token(token):
    """The account a verification link is for, or None if it's invalid, expired or the email has since changed."""
    try:
        data = signing.loads(token, salt=VERIFY_SALT, max_age=VERIFY_DAYS * 24 * 3600)
    except signing.BadSignature:
        return None
    account = MemberAccount.objects.filter(pk=data.get('a')).select_related('user').first()
    if account is None or normalise_email(account.email) != data.get('e'):
        return None
    return account


def send_verification(account):
    link = settings.SITE_URL.rstrip('/') + reverse('network_verify', kwargs={'token': verify_token(account)})
    context = {'account': account, 'link': link, 'days': VERIFY_DAYS}
    send_mail(
        'Confirm your email for Dojo',
        render_to_string('network/emails/verify.txt', context),
        None, [account.email],
        html_message=render_to_string('network/emails/verify.html', context),
    )


def mark_verified(account):
    if account.email_verified_at is None:
        account.email_verified_at = timezone.now()
        account.save(update_fields=['email_verified_at'])
    return link_memberships(account)


def link_memberships(account):
    """
    Link club membership records that use this account's (verified) email, as the member's own
    email or a guardian's, to the account. Records already linked to another account are left
    alone. Returns the members newly linked.

    Clubs already send portal links to these addresses, so a verified address is enough to show them.
    """
    if not account.is_verified:
        return []
    from members.models import Guardian, Member
    from . import details
    email = normalise_email(account.email)
    members = list(
        Member.objects.filter(is_active=True, account__isnull=True)
        .filter(Q(email__iexact=email) | Q(guardians__email__iexact=email)).distinct()
    )
    own = []
    for member in members:
        member.account = account
        if normalise_email(member.email) == email:
            member.account_role = Member.AccountRole.HOLDER
            own.append(member)
        else:
            member.account_role = Member.AccountRole.DEPENDANT
        member.save(update_fields=['account', 'account_role'])
    # A parent's guardian rows on their children's records follow the account from now on.
    Guardian.objects.filter(member__in=members, account__isnull=True, email__iexact=email).update(account=account)
    if own:
        details.adopt(account, own)   # start My details from what the clubs already hold, where they agree
    return members
