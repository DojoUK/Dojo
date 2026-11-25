"""
Birthday and "we miss you" emails to members.

Both are off until a club switches them on in Settings → Features, and members can switch them off
in their portal (Member.occasional_emails). The scheduler runs send_due() every hour; nothing goes
before SEND_FROM_HOUR, so no one is emailed in the middle of the night. Each email goes once: an
OccasionalEmail row is claimed before sending, so two schedulers can't both send it.

- Birthdays go on the day. Someone born on 29 February gets theirs on the 28th in other years.
- "We miss you" goes when a member's last session was the club's chosen number of days ago. It is
  sent in the four weeks after they cross that line, so switching it on doesn't email everyone who
  stopped coming long ago. Coming back and stopping again counts as a new absence. Members who have
  told the club they're leaving, or who have never trained, don't get it.
"""
import calendar
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.urls import reverse
from django.utils import timezone

SEND_FROM_HOUR = 9
WIN_BACK_WINDOW_DAYS = 28


def _date_text(day):
    return f'{day:%A} {day.day} {day:%B}'


def is_birthday(date_of_birth, today):
    if (date_of_birth.month, date_of_birth.day) == (today.month, today.day):
        return True
    # 29 February birthdays are celebrated on the 28th when there's no 29th.
    return ((date_of_birth.month, date_of_birth.day) == (2, 29) and (today.month, today.day) == (2, 28)
            and not calendar.isleap(today.year))


def _portal_links(member):
    site = settings.SITE_URL.rstrip('/')
    return (site + reverse('member_portal', kwargs={'token': member.token}),
            site + reverse('portal_my_details', kwargs={'token': member.token}) + '#club-emails')


def _send(member, kind, occasion, key, values, blocks=None):
    """Claim the email, then send it. Returns True if it went."""
    from mailer import compose

    from .emails import _recipient
    from .models import OccasionalEmail

    recipient = _recipient(member)
    if not recipient:
        return False
    try:
        with transaction.atomic():
            claim = OccasionalEmail.objects.create(member=member, kind=kind, occasion=occasion)
    except IntegrityError:
        return False                        # already sent (or being sent)
    portal_link, settings_link = _portal_links(member)
    try:
        compose.send(member.organisation, key, [recipient],
                     {**compose.member_values(member), 'portal_link': portal_link,
                      'settings_link': settings_link, **values},
                     blocks)
    except Exception:
        claim.delete()                      # try again at the next run
        return False
    return True


def _members(org):
    from .models import Member
    return Member.objects.filter(organisation=org, is_active=True, archived_at__isnull=True, occasional_emails=True)


def send_birthdays(org, today):
    from .models import OccasionalEmail
    match = Q(date_of_birth__month=today.month, date_of_birth__day=today.day)
    if (today.month, today.day) == (2, 28) and not calendar.isleap(today.year):
        match |= Q(date_of_birth__month=2, date_of_birth__day=29)
    sent = 0
    for member in _members(org).filter(match).exclude(date_of_birth__isnull=True):
        if is_birthday(member.date_of_birth, today) and _send(
                member, OccasionalEmail.Kind.BIRTHDAY, str(today.year), 'birthday',
                {'age': today.year - member.date_of_birth.year}):
            sent += 1
    return sent


def send_win_backs(org, today):
    from mailer import blocks

    from classes.models import Attendance

    from .models import OccasionalEmail
    days = org.win_back_days
    if not days:
        return 0
    newest = today - timedelta(days=days)
    oldest = newest - timedelta(days=WIN_BACK_WINDOW_DAYS)
    lapsed = (_members(org).exclude(leaving_requests__status='pending')
              .annotate(last_seen=Max('attendance__session__date', filter=Q(attendance__present=True)))
              .filter(last_seen__lte=newest, last_seen__gt=oldest))
    sent = 0
    for member in lapsed:
        last = (Attendance.objects.filter(member=member, present=True, session__date=member.last_seen)
                .select_related('session__assigned_class').first())
        portal_link, _ = _portal_links(member)
        if _send(member, OccasionalEmail.Kind.WIN_BACK, member.last_seen.isoformat(), 'win_back',
                 {'last_seen': _date_text(member.last_seen),
                  'class_name': last.session.assigned_class.name if last else '',
                  'days_away': (today - member.last_seen).days},
                 {'portal_button': blocks.portal_button(org, portal_link)}):
            sent += 1
    return sent


def send_due(now=None):
    """Send today's birthday and "we miss you" emails for every club that sends them. Returns how many went."""
    from organisations.models import Organisation
    now = timezone.localtime(now or timezone.now())
    if now.hour < SEND_FROM_HOUR:
        return 0
    today = now.date()
    sent = 0
    for org in Organisation.objects.all():
        if org.birthday_emails:
            sent += send_birthdays(org, today)
        if org.win_back_days:
            sent += send_win_backs(org, today)
    return sent
