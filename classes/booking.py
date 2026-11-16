"""
Member-facing class bookings: enrolling on a whole class (so they're on every
register), booking a single session, and cancelling either. Each action checks
the rules, makes the change and emails the member a confirmation. The portal
views are thin wrappers around these functions.
"""
from datetime import datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from mailer import blocks, compose

from .models import Attendance, Class, ClassMember, Session, SessionAbsence, SessionBooking, WaitingList


class BookingError(Exception):
    """A booking action that isn't allowed; the message is shown to the member."""


def session_start(session):
    """When a session starts: its date at the class's scheduled time for that weekday (midnight if none)."""
    start = time(0, 0)
    for entry in session.assigned_class.schedule or []:
        if entry.get('day') == session.date.weekday() and entry.get('time'):
            try:
                hours, minutes = entry['time'].split(':')[:2]
                start = time(int(hours), int(minutes))
            except (ValueError, TypeError):
                pass
            break
    return timezone.make_aware(datetime.combine(session.date, start))


def session_time_text(session):
    """The class's scheduled time for this session's weekday, e.g. '18:00–19:00' ('' if none)."""
    for entry in session.assigned_class.schedule or []:
        if entry.get('day') == session.date.weekday() and entry.get('time'):
            return f"{entry['time']}–{entry['end']}" if entry.get('end') else entry['time']
    return ''


def changes_closed(session, now=None):
    """True once members can no longer book or cancel this session themselves."""
    now = now or timezone.now()
    cutoff = timedelta(hours=session.assigned_class.organisation.booking_cutoff_hours)
    return now >= session_start(session) - cutoff


def bookable_sessions(cls, now=None):
    """Upcoming, uncancelled sessions of a class that still allow changes, soonest first."""
    today = timezone.localdate()
    sessions = (
        Session.objects.filter(assigned_class=cls, is_cancelled=False, date__gte=today)
        .select_related('assigned_class__organisation')
        .order_by('date')
    )
    return [s for s in sessions if not changes_closed(s, now)]


class Quota:
    """
    How many sessions a member's billing plan lets them be down for in a week or month.

    "Down for" means single-session bookings, sessions of classes they're fully enrolled on (unless
    they've cancelled that session) and past sessions they attended. No plan, or a plan with no
    limit, is unlimited.
    """

    def __init__(self, member, today=None):
        self.member = member
        self.today = today or timezone.localdate()
        self.policy = member.billing_policy
        self.limit = (self.policy.session_limit or None) if self.policy else None
        self.period = self.policy.limit_period if self.policy else 'month'
        self._cache = {}

    @property
    def limited(self):
        return self.limit is not None

    def bounds(self, day):
        """First and last day of the week (Monday to Sunday) or month containing `day`."""
        if self.period == 'week':
            start = day - timedelta(days=day.weekday())
            return start, start + timedelta(days=6)
        start = day.replace(day=1)
        return start, (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)

    def label(self, day):
        start, _ = self.bounds(day)
        return f'the week starting {start.day} {start:%B}' if self.period == 'week' else f'{start:%B %Y}'

    def used_ids(self, day):
        """Ids of the sessions the member is down for in the period containing `day`."""
        start, end = self.bounds(day)
        if start in self._cache:
            return self._cache[start]
        ids = set(
            SessionBooking.objects.filter(
                member=self.member, session__date__range=(start, end), session__is_cancelled=False,
            ).values_list('session_id', flat=True)
        )
        ids |= set(
            Attendance.objects.filter(
                member=self.member, present=True, session__date__range=(start, end),
                session__date__lt=self.today, session__is_cancelled=False,
            ).values_list('session_id', flat=True)
        )
        future_start = max(start, self.today)
        if future_start <= end:
            enrolled = list(ClassMember.objects.filter(member=self.member, auto_enrol=True)
                            .values_list('assigned_class_id', flat=True))
            ids |= set(
                Session.objects.filter(
                    assigned_class_id__in=enrolled, date__range=(future_start, end), is_cancelled=False,
                ).exclude(absences__member=self.member).values_list('pk', flat=True)
            )
        self._cache[start] = ids
        return ids

    def used(self, day=None):
        return len(self.used_ids(day or self.today))

    def can_add(self, session):
        if not self.limited:
            return True
        ids = self.used_ids(session.date)
        return session.pk in ids or len(ids) + 1 <= self.limit

    def check(self, sessions, what):
        """Raise BookingError if being down for `sessions` too would take the member over their plan's limit."""
        if not self.limited:
            return
        by_period = {}
        for session in sessions:
            by_period.setdefault(self.bounds(session.date)[0], []).append(session)
        for group in by_period.values():
            if len(self.used_ids(group[0].date) | {s.pk for s in group}) > self.limit:
                raise BookingError(
                    f'You can\'t {what}: your {self.policy.name} plan allows {self.policy.limit_text().lower()}, '
                    f'and this would take you over that for {self.label(group[0].date)}.'
                )


def session_headcount(session):
    """People expected at a session: everyone auto-enrolled, plus single-session bookings from anyone else."""
    auto = set(ClassMember.objects.filter(assigned_class=session.assigned_class, auto_enrol=True)
               .values_list('member_id', flat=True))
    auto -= set(SessionAbsence.objects.filter(session=session).values_list('member_id', flat=True))
    booked = set(SessionBooking.objects.filter(session=session).values_list('member_id', flat=True))
    return len(auto | booked)


def session_is_full(session):
    cls = session.assigned_class
    return cls.has_capacity_limit and session_headcount(session) >= cls.max_capacity


def _notify(member, key, values, box=None):
    """Email the member (or their guardian) a confirmation. A failed email never undoes the booking."""
    from members.emails import _recipient
    recipient = _recipient(member)
    if not recipient:
        return None
    try:
        compose.send(
            member.organisation, key, [recipient],
            {**compose.member_values(member), **values},
            {'booking_box': blocks.booking_box(*box)} if box else None,
        )
    except Exception:
        return None
    return recipient


def _date_text(day):
    return f'{day:%A} {day.day} {day:%B %Y}'


def _require_active(cls, member):
    if cls.organisation_id != member.organisation_id:
        raise BookingError('That class isn\'t available.')
    if not cls.is_active:
        raise BookingError(f'{cls.name} isn\'t open for booking.')
    reason = cls.ineligible_reason(member)
    if reason:
        raise BookingError(f'You can\'t book {cls.name}. {reason}')


def enrol(member, cls):
    """Enrol a member on every register for a class. Emails a confirmation."""
    _require_active(cls, member)
    with transaction.atomic():
        existing = ClassMember.objects.select_for_update().filter(assigned_class=cls, member=member).first()
        if existing and existing.auto_enrol:
            raise BookingError(f'You\'re already enrolled in {cls.name}.')
        if existing is None and cls.is_full:
            raise BookingError(f'{cls.name} is full, so it can\'t take new members right now.')
        upcoming = Session.objects.filter(assigned_class=cls, is_cancelled=False, date__gte=timezone.localdate())
        Quota(member).check(list(upcoming), f'enrol in {cls.name}')
        if existing:
            existing.auto_enrol = True
            existing.save(update_fields=['auto_enrol'])
        else:
            ClassMember.objects.create(assigned_class=cls, member=member, auto_enrol=True)
        WaitingList.objects.filter(assigned_class=cls, member=member).delete()
        # A whole-class enrolment makes single-session bookings for this class redundant.
        SessionBooking.objects.filter(session__assigned_class=cls, member=member).delete()
        SessionAbsence.objects.filter(session__assigned_class=cls, member=member).delete()
    schedule = cls.schedule_display()
    _notify(member, 'class_enrolled', {'class_name': cls.name, 'class_schedule': schedule}, (cls.name, schedule))


def cancel_enrolment(member, cls):
    """Take a member off a class. Emails a confirmation and offers the place to the waiting list."""
    with transaction.atomic():
        removed, _ = ClassMember.objects.filter(assigned_class=cls, member=member).delete()
        if not removed:
            raise BookingError(f'You\'re not enrolled in {cls.name}.')
        SessionAbsence.objects.filter(session__assigned_class=cls, member=member).delete()
        promoted = promote_from_waiting_list(cls)
    schedule = cls.schedule_display()
    _notify(member, 'class_enrolment_cancelled', {'class_name': cls.name, 'class_schedule': schedule},
            (cls.name, schedule))
    return promoted


def promote_from_waiting_list(cls):
    """Move the first person on a class's waiting list into the class (if there's space). Returns the member."""
    if cls.is_full:
        return None
    next_up = WaitingList.objects.filter(assigned_class=cls).select_related('member').first()
    if next_up is None:
        return None
    ClassMember.objects.get_or_create(assigned_class=cls, member=next_up.member)
    member = next_up.member
    next_up.delete()
    _notify(member, 'waiting_list_place', {'class_name': cls.name})
    return member


def book_session(member, session):
    """Book a member onto one session. Emails a confirmation."""
    cls = session.assigned_class
    _require_active(cls, member)
    if session.is_cancelled:
        raise BookingError('That session has been cancelled.')
    if changes_closed(session):
        raise BookingError('Booking for that session has closed.')
    with transaction.atomic():
        if ClassMember.objects.filter(assigned_class=cls, member=member, auto_enrol=True).exists():
            raise BookingError(f'You\'re enrolled in {cls.name}, so you\'re already on its registers.')
        if SessionBooking.objects.filter(session=session, member=member).exists():
            raise BookingError('You\'re already booked onto that session.')
        if session_is_full(session):
            raise BookingError('That session is full.')
        Quota(member).check([session], 'book this session')
        SessionBooking.objects.create(session=session, member=member)
    day = _date_text(session.date)
    _notify(member, 'session_booked', {'class_name': cls.name, 'session_date': day}, (cls.name, day))


def cancel_session_booking(member, session):
    """Cancel a single-session booking. Emails a confirmation."""
    if changes_closed(session):
        raise BookingError('That session is too close to cancel online: please contact the club.')
    deleted, _ = SessionBooking.objects.filter(session=session, member=member).delete()
    if not deleted:
        raise BookingError('You\'re not booked onto that session.')
    cls = session.assigned_class
    day = _date_text(session.date)
    _notify(member, 'session_booking_cancelled', {'class_name': cls.name, 'session_date': day}, (cls.name, day))


def _enrolled_session(member, session):
    if not ClassMember.objects.filter(assigned_class=session.assigned_class, member=member, auto_enrol=True).exists():
        raise BookingError('You\'re not enrolled in that class.')


def skip_session(member, session):
    """An enrolled member tells the club they can't make one session. Emails a confirmation."""
    _enrolled_session(member, session)
    if session.is_cancelled:
        raise BookingError('That session has been cancelled.')
    if changes_closed(session):
        raise BookingError('That session is too close to cancel online: please contact the club.')
    _, created = SessionAbsence.objects.get_or_create(session=session, member=member)
    if not created:
        raise BookingError('You\'ve already cancelled that session.')
    cls = session.assigned_class
    day = _date_text(session.date)
    _notify(member, 'session_booking_cancelled', {'class_name': cls.name, 'session_date': day}, (cls.name, day))


def rejoin_session(member, session):
    """Undo skip_session, if the session is still open and has space. Emails a confirmation."""
    _enrolled_session(member, session)
    if changes_closed(session):
        raise BookingError('Booking for that session has closed.')
    if not SessionAbsence.objects.filter(session=session, member=member).exists():
        raise BookingError('You\'re already down to attend that session.')
    if session_is_full(session):
        raise BookingError('That session is full.')
    Quota(member).check([session], 'rejoin this session')
    SessionAbsence.objects.filter(session=session, member=member).delete()
    cls = session.assigned_class
    day = _date_text(session.date)
    _notify(member, 'session_booked', {'class_name': cls.name, 'session_date': day}, (cls.name, day))
