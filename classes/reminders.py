"""
Reminder emails before sessions. The scheduler runs send_due() every few minutes; each club sets in
Settings → Features how many hours before a session reminders go (or switches them off), and
whether they go to people booked onto that session or to everyone expected there. Members can
switch them off in their portal. Each person is reminded once per session.
"""
from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from mailer import blocks

from .booking import _notify, session_email, session_start
from .models import ClassMember, Session, SessionAbsence, SessionBooking, SessionReminder


def _expected(session, window_opened, scope):
    """The members to remind about a session: confirmed bookings made before the reminder window
    opened (anyone booking later has just had a confirmation), plus, for 'everyone', members enrolled
    on the whole class who haven't said they can't come."""
    members = {b.member for b in SessionBooking.objects.filter(
        session=session, status=SessionBooking.Status.CONFIRMED, created_at__lte=window_opened).select_related('member')}
    if scope == 'everyone':
        away = set(SessionAbsence.objects.filter(session=session).values_list('member_id', flat=True))
        members |= {cm.member for cm in ClassMember.objects.filter(assigned_class=session.assigned_class, auto_enrol=True)
                    .select_related('member') if cm.member_id not in away}
    already = set(SessionReminder.objects.filter(session=session).values_list('member_id', flat=True))
    return [m for m in members if m.is_active and m.session_reminders and m.pk not in already]


def _portal_link(member):
    return settings.SITE_URL.rstrip('/') + reverse('portal_bookings', kwargs={'token': member.token})


def send_due(now=None):
    """Send every reminder that's due. Returns how many people were reminded."""
    from organisations.models import Organisation
    now = now or timezone.now()
    sent = 0
    for org in Organisation.objects.all():
        hours = org.session_reminder_hours
        if not hours:
            continue
        ahead = timedelta(hours=hours)
        sessions = (Session.objects.filter(assigned_class__organisation=org, assigned_class__is_active=True,
                                           is_cancelled=False, date__gte=timezone.localdate(now) - timedelta(days=1),
                                           date__lte=timezone.localdate(now + ahead) + timedelta(days=1))
                    .select_related('assigned_class'))
        for session in sessions:
            start = session_start(session)
            if not (now < start <= now + ahead):
                continue
            values, box = session_email(session)
            for member in _expected(session, start - ahead, org.session_reminder_scope):
                link = _portal_link(member)
                recipient = _notify(member, 'session_reminder', {**values, 'portal_link': link}, box,
                                    extra_blocks={'portal_button': blocks.portal_button(org, link)})
                SessionReminder.objects.get_or_create(session=session, member=member,
                                                      defaults={'sent_to': recipient or ''})
                sent += bool(recipient)
    return sent
