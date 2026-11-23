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


# How long a single session held for an unpaid checkout is kept before it's released. Stripe's
# checkout page is set to expire a little sooner than this, so it can't be paid after release.
PAYMENT_HOLD_MINUTES = 35
CHECKOUT_MINUTES = 32


def needs_plan(member):
    """Members of a club that uses billing must be on a plan to enrol on a whole class."""
    return member.organisation.members_need_plan and member.billing_policy_id is None


def single_session_payable(org):
    """Whether a member can pay for one session online: a price is set and Stripe is connected. Used by
    members with no plan, and by members booking more sessions than their plan's limit."""
    from billing import payments
    return org.single_session_price is not None and payments.stripe_keys(org) is not None


def extra_sessions_payable(member):
    """Whether a member on a session-limited plan can book past the limit by paying the single-session price."""
    policy = member.billing_policy
    return bool(policy and policy.limits_sessions) and single_session_payable(member.organisation)


def _drop_pending(booking):
    """Remove an unpaid booking and cancel its invoice. If it was paid after all, confirm it instead."""
    invoice = booking.invoice
    if invoice is not None:
        invoice.refresh_from_db()
        if invoice.status == 'paid':
            confirm_paid(invoice)
            return False
        if invoice.status != 'cancelled':
            invoice.status = 'cancelled'
            invoice.cancelled_at = timezone.now()
            invoice.cancel_reason = 'Single session booking was not paid for.'
            invoice.save(update_fields=['status', 'cancelled_at', 'cancel_reason'])
    booking.delete()
    return True


def release_expired():
    """Let go of places held for payments that never came, so the places can be booked again."""
    cutoff = timezone.now() - timedelta(minutes=PAYMENT_HOLD_MINUTES)
    for booking in SessionBooking.objects.filter(status=SessionBooking.Status.PENDING, created_at__lt=cutoff) \
            .select_related('invoice'):
        _drop_pending(booking)


def release_for_invoice(invoice):
    """A checkout was abandoned or expired: release the place held for it (if still unpaid)."""
    for booking in SessionBooking.objects.filter(invoice=invoice, status=SessionBooking.Status.PENDING):
        _drop_pending(booking)


def confirm_paid(invoice):
    """An invoice for a single session has been paid (Stripe's webhook): confirm the booking and email the member."""
    confirmed = []
    for booking in SessionBooking.objects.filter(invoice=invoice, status=SessionBooking.Status.PENDING) \
            .select_related('session__assigned_class', 'member__organisation'):
        booking.status = SessionBooking.Status.CONFIRMED
        booking.save(update_fields=['status'])
        _notify(booking.member, 'session_booked', *session_email(booking.session))
        confirmed.append(booking)
    return confirmed


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
        # Only a session-limited plan counts sessions; a class-limited one is checked by class_limit_message().
        self.limit = self.policy.session_limit if self.policy and self.policy.limits_sessions else None
        self.period = self.policy.limit_period if self.policy else 'month'
        # A family plan's limit is shared: it counts everyone in the family who is on the same plan.
        self.sharers = [member]
        if self.policy and self.policy.is_family_plan and self.limit:
            from members.family import family_members
            self.sharers = [m for m in family_members(member) if m.billing_policy_id == self.policy.pk] or [member]
        self.shared = len(self.sharers) > 1
        self._cache = {}

    @property
    def limited(self):
        return self.limit is not None

    def bounds(self, day):
        """First and last day of the week (Monday to Sunday) or calendar month containing `day`."""
        if self.period == 'week':
            start = day - timedelta(days=day.weekday())
            return start, start + timedelta(days=6)
        start = day.replace(day=1)
        return start, (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)

    def label(self, day):
        """'the week Monday 16 to Sunday 22 November', or 'November 2026'."""
        start, end = self.bounds(day)
        if self.period != 'week':
            return f'{start:%B %Y}'
        if start.month == end.month:
            return f'the week Monday {start.day} to Sunday {end.day} {end:%B}'
        return f'the week Monday {start.day} {start:%B} to Sunday {end.day} {end:%B}'

    def _member_ids(self, member, start, end):
        ids = set(
            SessionBooking.objects.filter(
                member=member, session__date__range=(start, end), session__is_cancelled=False,
            ).values_list('session_id', flat=True)
        )
        ids |= set(
            Attendance.objects.filter(
                member=member, present=True, session__date__range=(start, end),
                session__date__lt=self.today, session__is_cancelled=False,
            ).values_list('session_id', flat=True)
        )
        future_start = max(start, self.today)
        if future_start <= end:
            enrolled = list(ClassMember.objects.filter(member=member, auto_enrol=True)
                            .values_list('assigned_class_id', flat=True))
            ids |= set(
                Session.objects.filter(
                    assigned_class_id__in=enrolled, date__range=(future_start, end), is_cancelled=False,
                ).exclude(absences__member=member).values_list('pk', flat=True)
            )
        return ids

    def used_ids(self, day):
        """(member id, session id) pairs the plan's holders are down for in the period containing `day`."""
        start, end = self.bounds(day)
        if start not in self._cache:
            self._cache[start] = {
                (m.pk, session_id) for m in self.sharers for session_id in self._member_ids(m, start, end)
            }
        return self._cache[start]

    def used(self, day=None):
        return len(self.used_ids(day or self.today))

    def can_add(self, session):
        if not self.limited:
            return True
        ids = self.used_ids(session.date)
        return (self.member.pk, session.pk) in ids or len(ids) + 1 <= self.limit

    def check(self, sessions, what):
        """Raise BookingError if being down for `sessions` too would take the member over their plan's limit."""
        if not self.limited:
            return
        by_period = {}
        for session in sessions:
            by_period.setdefault(self.bounds(session.date)[0], []).append(session)
        for group in by_period.values():
            if len(self.used_ids(group[0].date) | {(self.member.pk, s.pk) for s in group}) > self.limit:
                shared = ' (shared by your family),' if self.shared else ','
                raise BookingError(
                    f'You can\'t {what}: your {self.policy.name} plan allows {self.policy.limit_text().lower()}{shared} '
                    f'and this would take you over that for {self.label(group[0].date)}.'
                )


def enrolled_class_count(member, excluding=None):
    """How many classes the member is enrolled on (every register), not counting `excluding`."""
    enrolled = ClassMember.objects.filter(member=member, auto_enrol=True)
    if excluding is not None:
        enrolled = enrolled.exclude(assigned_class=excluding)
    return enrolled.count()


def class_limit_message(member, cls):
    """
    Why the member's plan stops them enrolling in `cls`, or '' if it doesn't. A class-limited plan
    caps how many classes they're enrolled on at once; single-session bookings don't count, and
    leaving a class frees a place for another. On a family plan each member has their own allowance.
    """
    policy = member.billing_policy
    if not policy or not policy.limits_classes:
        return ''
    enrolled = enrolled_class_count(member, excluding=cls)
    if enrolled < policy.class_limit:
        return ''
    return (f'You can\'t enrol in {cls.name}: your {policy.name} plan allows {policy.limit_text().lower()} '
            f'and you\'re already enrolled in {enrolled}. Leave a class to join another, '
            f'or book single sessions of {cls.name}.')


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


def _notify(member, key, values, box=None, extra_blocks=None):
    """Email the member (or their guardian) a confirmation. A failed email never undoes the booking."""
    from members.emails import _recipient
    recipient = _recipient(member)
    if not recipient:
        return None
    email_blocks = {'booking_box': blocks.booking_box(*box)} if box else {}
    email_blocks.update(extra_blocks or {})
    try:
        compose.send(
            member.organisation, key, [recipient],
            {**compose.member_values(member), **values},
            email_blocks or None,
        )
    except Exception:
        return None
    return recipient


def _date_text(day):
    return f'{day:%A} {day.day} {day:%B %Y}'


def session_email(session):
    """(values, box) for emails about one session: its class, date and start time."""
    cls = session.assigned_class
    day = _date_text(session.date)
    when = session_time_text(session)
    return ({'class_name': cls.name, 'session_date': day, 'session_time': when},
            (cls.name, f'{day}, {when}' if when else day))


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
    if needs_plan(member):
        raise BookingError(f'Choose a plan before you enrol in {cls.name}. Without a plan you can still book '
                           f'single sessions, one at a time.')
    with transaction.atomic():
        existing = ClassMember.objects.select_for_update().filter(assigned_class=cls, member=member).first()
        if existing and existing.auto_enrol:
            raise BookingError(f'You\'re already enrolled in {cls.name}.')
        if existing is None and cls.is_full:
            raise BookingError(f'{cls.name} is full, so it can\'t take new members right now.')
        upcoming = Session.objects.filter(assigned_class=cls, is_cancelled=False, date__gte=timezone.localdate())
        Quota(member).check(list(upcoming), f'enrol in {cls.name}')
        too_many = class_limit_message(member, cls)
        if too_many:
            raise BookingError(too_many)
        if existing:
            existing.auto_enrol = True
            existing.save(update_fields=['auto_enrol'])
        else:
            ClassMember.objects.create(assigned_class=cls, member=member, auto_enrol=True)
        WaitingList.objects.filter(assigned_class=cls, member=member).delete()
        # A whole-class enrolment makes single-session bookings for this class redundant.
        for pending in SessionBooking.objects.filter(session__assigned_class=cls, member=member,
                                                     status=SessionBooking.Status.PENDING):
            _drop_pending(pending)
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
    """
    Book a member onto one session and return the SessionBooking.

    A member on a plan is booked straight away and emailed a confirmation. Two kinds of booking have to
    be paid for first, at the club's single-session price, if the club has set that price and connected
    Stripe: one by a member of a club that uses billing who has no plan, and one that would take a member
    past their plan's session limit for that week or month (an extra session). Such a booking is returned
    as 'pending', holding the place, with an unpaid invoice attached. It is confirmed (and the
    confirmation emailed) only when Stripe's webhook reports that invoice paid (see confirm_paid).
    Without the price and Stripe, a member with no plan is booked straight away, and one at their
    plan's limit is refused.
    """
    from billing.models import Invoice
    cls = session.assigned_class
    org = member.organisation
    _require_active(cls, member)
    if session.is_cancelled:
        raise BookingError('That session has been cancelled.')
    if changes_closed(session):
        raise BookingError('Booking for that session has closed.')
    release_expired()
    # No plan means paying for the session first, but only if the club has set a price and connected
    # Stripe; until it has, single sessions are booked straight away without payment.
    pay_first = needs_plan(member) and single_session_payable(org)
    with transaction.atomic():
        if ClassMember.objects.filter(assigned_class=cls, member=member, auto_enrol=True).exists():
            raise BookingError(f'You\'re enrolled in {cls.name}, so you\'re already on its registers.')
        existing = SessionBooking.objects.filter(session=session, member=member).first()
        if existing:
            raise BookingError('You already have a booking for that session waiting for payment: finish paying '
                               'for it, or cancel it and start again.' if existing.is_pending
                               else 'You\'re already booked onto that session.')
        if session_is_full(session):
            raise BookingError('That session is full.')
        extra = False
        try:
            Quota(member).check([session], 'book this session')
        except BookingError:
            if not extra_sessions_payable(member):
                raise
            extra = pay_first = True   # past the plan's limit: an extra session, paid for now
        if not pay_first:
            booking = SessionBooking.objects.create(session=session, member=member)
        else:
            day = _date_text(session.date)
            invoice = Invoice(
                organisation=org, member=member, amount=org.single_session_price, period=f'{cls.name}, {day}',
                due_date=timezone.localdate(),
                notes=('Extra session beyond the member\'s plan limit, booked online. The booking is confirmed when this is paid.'
                       if extra else
                       'Single session booked online by the member. The booking is confirmed when this is paid.'),
            )
            invoice.skip_autopay = True  # paid at checkout, never collected automatically
            invoice.save()
            booking = SessionBooking.objects.create(
                session=session, member=member, status=SessionBooking.Status.PENDING, invoice=invoice,
            )
    if not booking.is_pending:
        _notify(member, 'session_booked', *session_email(session))
    return booking


def start_payment(booking, back_url):
    """The Stripe Checkout URL for a pending booking. If Stripe can't be reached the held place is released."""
    import stripe

    from billing import payments
    try:
        return payments.start_checkout(
            booking.invoice, success_url=f'{back_url}?paid=1', cancel_url=back_url, minutes=CHECKOUT_MINUTES,
            metadata={'session_booking': str(booking.pk)},
        )
    except (stripe.error.StripeError, ValueError):
        _drop_pending(booking)
        raise BookingError('We couldn\'t start the payment, so nothing has been booked. Please try again, '
                           'or contact the club.')


def cancel_session_booking(member, session):
    """
    Cancel a single-session booking. Emails a confirmation. Returns the paid invoice if the member had
    paid for the session (nothing is refunded automatically), otherwise None. A booking still waiting
    for payment is simply dropped, with its invoice.
    """
    pending = SessionBooking.objects.filter(session=session, member=member,
                                            status=SessionBooking.Status.PENDING).select_related('invoice').first()
    if pending:
        _drop_pending(pending)
        return None
    if changes_closed(session):
        raise BookingError('That session is too close to cancel online: please contact the club.')
    booking = SessionBooking.objects.filter(session=session, member=member).select_related('invoice').first()
    if booking is None:
        raise BookingError('You\'re not booked onto that session.')
    paid_invoice = booking.invoice if booking.invoice and booking.invoice.status == 'paid' else None
    booking.delete()
    cls = session.assigned_class
    _notify(member, 'session_booking_cancelled', *session_email(session))
    return paid_invoice


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
    _notify(member, 'session_booking_cancelled', *session_email(session))


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
    _notify(member, 'session_booked', *session_email(session))
