"""
The club dashboard: a grid of widgets that each club arranges for itself.

Each kind of screen has its own arrangement of the same widgets:

- 'wide', for computers and tablets held sideways: 24 columns, from WIDE_FROM_PX.
- 'tablet', for tablets held upright and narrow windows: 12 columns, from TABLET_FROM_PX.
- phones, narrower than that: one widget per row in the wide layout's order (no arranging).

Columns stretch across the space there is, and rows grow and shrink with them, so cells stay
roughly square. A layout is a list of {'key', 'x', 'y', 'w', 'h'} in columns and rows, saved in
Organisation.settings['dashboard'] as 'widgets' (wide) and, once someone has arranged it on a
tablet, 'tablet'. Until then the tablet layout is worked out from the wide one (tablet_from_wide).

Every widget has a narrowest width it reads well at (min_px). Each size's minimum width in columns
comes from that and the narrowest screen of that size, so a layout always fits as arranged: what
Customise shows is what everyone sees. A club without a layout gets the default, packed to fit the
widgets it can use.

Admins arrange the club's layout. Everyone else sees the same layout with
the widgets they aren't allowed to see taken out and the rest moved up.
Only the widgets on the layout are worked out, so a lean dashboard is a
fast one.

Some widgets report on parts of Dojo that not every build has (the Dojo
network's reviews, the "Not seen lately" report). Those say what they need
and are left out where it isn't there, so this module works on any branch.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import json
import math

from django.apps import apps
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils.functional import cached_property

COLUMNS = 24                # the wide layout
TABLET_COLUMNS = 12
WIDE_FROM_PX = 880          # grid width: an 11" iPad held sideways (beside the sidebar) is wide
TABLET_FROM_PX = 480        # below this, phones: one widget per row
# Each size's narrowest cell, which sets how many columns a widget needs to stay readable.
MIN_CELL_PX = {'wide': WIDE_FROM_PX / COLUMNS, 'tablet': TABLET_FROM_PX / TABLET_COLUMNS}
SIZE_COLUMNS = {'wide': COLUMNS, 'tablet': TABLET_COLUMNS}
MAX_ROWS = 400
SETTINGS_KEY = 'dashboard'
LAYOUT_VERSION = 3
# Version 1 layouts were measured in fixed 50px cells and laid out for a screen about 20 cells wide.
V1_COLUMNS = 20

CATEGORIES = (
    ('numbers', 'Key numbers'),
    ('lists', 'Lists and alerts'),
    ('charts', 'Charts'),
)


@dataclass(frozen=True)
class Widget:
    key: str
    title: str
    description: str
    icon: str
    category: str
    size: tuple                 # default (w, h) in cells
    min_size: tuple
    data: object = None         # fn(board) -> dict for the template
    min_px: int = 200           # narrowest it reads well at; narrower screens widen it
    max_size: tuple = (COLUMNS, 24)
    admin_only: bool = False
    capability: str = ''        # admins, or staff granted this, can see it
    features: tuple = ()        # every one must be switched on
    fields: tuple = ()          # optional member/staff fields, any one switched on
    needs_apps: tuple = ()      # Django apps that must be installed

    @property
    def template(self):
        return f'org/widgets/{self.key}.html'

    def available(self, org):
        """Whether this build and the club have what this widget reports on."""
        if any(not apps.is_installed(a) for a in self.needs_apps):
            return False
        if any(not org.has_feature(f) for f in self.features):
            return False
        return not self.fields or any(org.has_field(f) for f in self.fields)

    def visible(self, board):
        if not self.available(board.org):
            return False
        if self.admin_only and not board.is_admin:
            return False
        if self.capability and not board.can(self.capability):
            return False
        return True

    def min_w(self, size='wide'):
        """Fewest columns, so it's never narrower than min_px on the narrowest screen of this size."""
        own = self.min_size[0] if size == 'wide' else math.ceil(self.min_size[0] / 2)
        return min(SIZE_COLUMNS[size], max(own, math.ceil(self.min_px / MIN_CELL_PX[size])))

    def max_w(self, size='wide'):
        own = self.max_size[0] if size == 'wide' else math.ceil(self.max_size[0] / 2)
        return max(self.min_w(size), min(SIZE_COLUMNS[size], own))

    def default_w(self, size='wide'):
        w = self.size[0] if size == 'wide' else math.ceil(self.size[0] / 2)
        return max(self.min_w(size), min(self.max_w(size), w))

    def clamp(self, w, h, size='wide'):
        return (max(self.min_w(size), min(self.max_w(size), w)),
                max(self.min_size[1], min(self.max_size[1], h)))

    def limits(self):
        """What the page needs to arrange it at each size."""
        return {
            'minW': self.min_w(), 'maxW': self.max_w(),
            'tabletMinW': self.min_w('tablet'), 'tabletMaxW': self.max_w('tablet'),
            'minH': self.min_size[1], 'maxH': self.max_size[1],
        }

    def catalogue_entry(self):
        return {
            'key': self.key, 'title': self.title, 'description': self.description,
            'icon': self.icon, 'category': self.category,
            'w': self.default_w(), 'h': self.size[1], 'tabletW': self.default_w('tablet'),
            'minPx': self.min_px, **self.limits(),
        }


class Board:
    """What the widgets need to know about the person looking, worked out once per page."""

    def __init__(self, org, user, membership, today=None):
        self.org = org
        self.user = user
        self.membership = membership
        self.today = today or date.today()

    @cached_property
    def is_admin(self):
        # No membership means a superuser looking in, as in OrgMixin.
        return self.membership is None or self.membership.is_org_admin or self.user.is_superuser

    def can(self, capability):
        if self.is_admin:
            return True
        return self.membership is not None and self.membership.has_capability(capability)

    @cached_property
    def class_ids(self):
        """The classes this person's numbers cover: all of them for admins, their own for coaches."""
        from classes.models import Class, ClassCoach
        if self.is_admin:
            return list(Class.objects.filter(organisation=self.org).values_list('pk', flat=True))
        return list(ClassCoach.objects.filter(
            user=self.user, assigned_class__organisation=self.org,
        ).values_list('assigned_class_id', flat=True).distinct())

    @cached_property
    def not_seen(self):
        """{'not_seen', 'never', 'slowing'}: rows of members absent for 2 weeks or more."""
        try:
            from members import not_seen
        except ImportError:
            return _not_seen_here(self)
        return not_seen.report(self.org, days=14, today=self.today, describe=False)

    @cached_property
    def active_members(self):
        return self.org.club_members.filter(is_active=True)

    @property
    def first_of_month(self):
        return self.today.replace(day=1)

    def months_back(self, count):
        """First days of the last `count` months, oldest first, ending with this month."""
        months, first = [], self.first_of_month
        for _ in range(count):
            months.append(first)
            first = (first - timedelta(days=1)).replace(day=1)
        return months[::-1]


def _not_seen_here(board):
    """The "Not seen lately" groups for builds without members/not_seen.py: active members whose
    last session was 2 or more weeks ago, and those who joined that long ago and never came."""
    cutoff = board.today - timedelta(days=14)
    groups = {'not_seen': [], 'never': [], 'slowing': []}
    members = board.active_members.annotate(
        last_seen=Max('attendance__session__date', filter=Q(attendance__present=True)),
    )
    for member in members:
        last = member.last_seen
        row = {'member': member, 'last_seen': last, 'days_since': (board.today - last).days if last else None}
        if last is None:
            if member.joined_date is None or member.joined_date <= cutoff:
                groups['never'].append(row)
        elif last <= cutoff:
            groups['not_seen'].append(row)
    groups['not_seen'].sort(key=lambda r: r['last_seen'])
    groups['never'].sort(key=lambda r: r['member'].name)
    return groups


def star_icons(rating):
    """Bootstrap icon names for five stars showing a rating to the nearest half star."""
    halves = round(float(rating or 0) * 2)
    return ['bi-star-fill' if halves >= n * 2 else 'bi-star-half' if halves == n * 2 - 1 else 'bi-star'
            for n in range(1, 6)]


def _chart(kind, labels, series, prefix='', stacked=False):
    return json.dumps({'type': kind, 'labels': labels, 'series': series, 'prefix': prefix, 'stacked': stacked})


def _attendance(board, start, end):
    """(present, recorded) attendance marks for this person's classes between two dates."""
    from classes.models import Attendance
    rows = Attendance.objects.filter(
        session__assigned_class_id__in=board.class_ids, session__date__gte=start, session__date__lte=end,
    ).aggregate(total=Count('pk'), present=Count('pk', filter=Q(present=True)))
    return rows['present'], rows['total']


def _month(value):
    """TruncMonth gives a date for a date field and a datetime for a datetime field."""
    if hasattr(value, 'hour'):
        value = value.date()
    return value.replace(day=1)


def _rate(present, total):
    return round(present / total * 100) if total else None


# ── Key numbers ──────────────────────────────────────────────────────────

def active_members(board):
    return {
        'member_count': board.active_members.count(),
        'new_members_month': board.active_members.filter(joined_date__gte=board.first_of_month).count(),
    }


def sessions_week(board):
    from classes.models import Session
    four_weeks_ago = board.today - timedelta(weeks=4)
    present, total = _attendance(board, four_weeks_ago, board.today)
    return {
        'sessions_this_week': Session.objects.filter(
            assigned_class_id__in=board.class_ids, date__gte=board.today,
            date__lte=board.today + timedelta(days=7),
        ).count(),
        'attendance_rate': _rate(present, total),
    }


def revenue_month(board):
    from billing.models import Invoice, Payment
    return {
        'revenue_month': Payment.objects.filter(
            invoice__organisation=board.org, paid_at__date__gte=board.first_of_month,
        ).aggregate(t=Sum('amount'))['t'] or 0,
        'outstanding': Invoice.objects.filter(
            organisation=board.org, status=Invoice.Status.UNPAID,
        ).aggregate(t=Sum('amount'))['t'] or 0,
    }


def outstanding_invoices(board):
    from billing.models import Invoice
    unpaid = Invoice.objects.filter(organisation=board.org, status=Invoice.Status.UNPAID)
    totals = unpaid.aggregate(
        unpaid_count=Count('pk'), unpaid_amount=Sum('amount'),
        overdue_count=Count('pk', filter=Q(due_date__lt=board.today)),
        overdue_amount=Sum('amount', filter=Q(due_date__lt=board.today)),
    )
    return {
        'unpaid_count': totals['unpaid_count'], 'unpaid_amount': totals['unpaid_amount'] or 0,
        'overdue_count': totals['overdue_count'], 'overdue_amount': totals['overdue_amount'] or 0,
    }


def at_risk(board):
    groups = board.not_seen
    try:
        from members import not_seen  # noqa: F401  (only this report spots members coming less often)
        tracks_slowing = True
    except ImportError:
        tracks_slowing = False
    return {
        'tracks_slowing': tracks_slowing,
        'at_risk_count': sum(len(rows) for rows in groups.values()),
        'at_risk_groups': {name: len(rows) for name, rows in groups.items()},
    }


def attendance_rate(board):
    four_weeks_ago = board.today - timedelta(weeks=4)
    eight_weeks_ago = board.today - timedelta(weeks=8)
    present, total = _attendance(board, four_weeks_ago + timedelta(days=1), board.today)
    before_present, before_total = _attendance(board, eight_weeks_ago + timedelta(days=1), four_weeks_ago)
    rate, before = _rate(present, total), _rate(before_present, before_total)
    return {
        'rate_now': rate,
        'rate_change': rate - before if rate is not None and before is not None else None,
        'rate_marks': present,
    }


def new_members(board):
    last_month = (board.first_of_month - timedelta(days=1)).replace(day=1)
    members = board.org.club_members
    return {
        'joined_this_month': members.filter(joined_date__gte=board.first_of_month).count(),
        'joined_last_month': members.filter(
            joined_date__gte=last_month, joined_date__lt=board.first_of_month,
        ).count(),
    }


def club_rating(board):
    from network import reviews
    rating = reviews.summary(board.org)
    return {'rating': rating, 'rating_stars': star_icons(rating['average'])}


# ── Lists and alerts ─────────────────────────────────────────────────────

def single_session_setup(org):
    """What a club offering class booking still has to set up before members without a plan pay
    per session: 'price' and/or 'stripe'."""
    from billing.payments import stripe_keys
    missing = []
    if not hasattr(org, 'single_session_price'):        # a build without class booking
        return missing
    if org.has_feature('billing') and org.has_bookable_classes:
        if org.single_session_price is None:
            missing.append('price')
        if stripe_keys(org) is None:
            missing.append('stripe')
    return missing


def needs_attention(board):
    """The warnings that used to sit across the top of the dashboard."""
    from members.models import MemberApplication
    from .models import OrganisationMember

    org, today, active = board.org, board.today, board.active_members
    thirty_days = today + timedelta(days=30)

    track_licences = org.has_field('licence')
    staff_checks = [
        (expiry_field, label)
        for key, expiry_field, label in (
            ('dbs', 'dbs_expiry', 'DBS'),
            ('coaching_licence', 'coaching_licence_expiry', org.coaching_licence_label),
        )
        if org.has_field(key)
    ]
    staff_expiring = staff_expired = 0
    if staff_checks:
        staff = OrganisationMember.objects.filter(organisation=org)
        expiring_q, expired_q = Q(pk__in=[]), Q(pk__in=[])
        for expiry_field, _ in staff_checks:
            expiring_q |= Q(**{f'{expiry_field}__lte': thirty_days, f'{expiry_field}__gte': today})
            expired_q |= Q(**{f'{expiry_field}__lt': today})
        staff_expiring = staff.filter(expiring_q).count()
        staff_expired = staff.filter(expired_q).count()

    unsigned_waivers_count = 0
    if org.has_feature('documents'):
        from documents.models import SignedWaiver, WaiverTemplate
        required = WaiverTemplate.objects.filter(organisation=org, is_active=True, is_required=True)
        if required.exists():
            signed = SignedWaiver.objects.filter(template__in=required).values_list('member_id', flat=True)
            unsigned_waivers_count = active.exclude(pk__in=set(signed)).count()

    data = {
        'missing_location': not getattr(org, 'has_location', True),   # builds without club locations skip this
        'attention_single_session': single_session_setup(org),
        'unsigned_waivers_count': unsigned_waivers_count,
        'pending_applications': MemberApplication.objects.filter(
            organisation=org, status=MemberApplication.Status.PENDING,
        ).count() if org.has_feature('applications') else 0,
        'licences_expiring': active.filter(
            licence_expiry__lte=thirty_days, licence_expiry__gte=today,
        ).count() if track_licences else 0,
        'licences_expired': active.filter(licence_expiry__lt=today).count() if track_licences else 0,
        'staff_expiring': staff_expiring,
        'staff_expired': staff_expired,
        'staff_check_names': ' or '.join(label for _, label in staff_checks),
    }
    if org.has_feature('billing'):
        from billing.autopay import needing_attention
        failed, expiring = needing_attention(org, today)
        data['payments_failed'] = failed.count()
        data['cards_expiring'] = expiring.count()
    data['all_clear'] = not any(v for k, v in data.items() if k != 'staff_check_names')
    return data


def upcoming_sessions(board):
    from classes.models import Attendance, ClassMember, Session
    sessions = Session.objects.filter(
        assigned_class_id__in=board.class_ids, date__gte=board.today,
        date__lte=board.today + timedelta(days=7),
    ).select_related('assigned_class').order_by('date', 'assigned_class__name')
    rows = []
    for session in sessions:
        marks = Attendance.objects.filter(session=session).aggregate(
            total=Count('pk'), present=Count('pk', filter=Q(present=True)),
        )
        rows.append({
            'session': session,
            'taken': bool(marks['total']),
            'enrolled': ClassMember.objects.filter(assigned_class_id=session.assigned_class_id).count(),
            'present': marks['present'] if marks['total'] else None,
        })
    return {'upcoming': rows}


def quick_actions(board):
    return {}


def not_seen_list(board):
    groups = board.not_seen
    rows = [dict(row, group='Not seen') for row in groups['not_seen']] \
        + [dict(row, group='Coming less often') for row in groups['slowing']] \
        + [dict(row, group='Never come') for row in groups['never']]
    return {'not_seen_rows': rows[:25], 'not_seen_more': max(0, len(rows) - 25)}


def applications_list(board):
    from members.models import MemberApplication
    pending = MemberApplication.objects.filter(
        organisation=board.org, status=MemberApplication.Status.PENDING,
    ).order_by('-submitted_at')
    return {'applications': list(pending[:10]), 'applications_count': pending.count()}


def upcoming_gradings(board):
    from progression.models import GradingSession
    sessions = GradingSession.objects.filter(
        grading__organisation=board.org, date__gte=board.today,
    ).select_related('grading', 'grading__system').annotate(
        candidate_count=Count('candidates', distinct=True),
    ).order_by('date', 'pk')[:10]
    return {'grading_sessions': list(sessions)}


def birthdays(board):
    """Active members with a birthday in the next 14 days, soonest first."""
    soon = []
    for member in board.active_members.exclude(date_of_birth__isnull=True):
        dob = member.date_of_birth
        for year in (board.today.year, board.today.year + 1):
            try:
                day = dob.replace(year=year)
            except ValueError:          # 29 February outside a leap year
                day = date(year, 3, 1)
            if day >= board.today:
                break
        days = (day - board.today).days
        if days <= 14:
            soon.append({'member': member, 'day': day, 'days': days, 'age': day.year - dob.year})
    soon.sort(key=lambda row: (row['days'], row['member'].name))
    return {'birthdays': soon}


def recent_reviews(board):
    from network.models import ClubReview
    reviews = list(ClubReview.objects.filter(organisation=board.org).select_related('account')
                   .order_by('-updated_at')[:5])
    for review in reviews:
        review.stars = star_icons(review.rating)
    return {'latest_reviews': reviews}


def class_attendance(board):
    """Average members at each class's sessions over the last four weeks, busiest first."""
    from classes.models import Class
    start = board.today - timedelta(weeks=4)
    classes = Class.objects.filter(pk__in=board.class_ids).annotate(
        run=Count('sessions', filter=Q(
            sessions__date__gte=start, sessions__date__lte=board.today, sessions__is_cancelled=False,
            sessions__attendance__isnull=False,
        ), distinct=True),
        present=Count('sessions__attendance', filter=Q(
            sessions__date__gte=start, sessions__date__lte=board.today,
            sessions__attendance__present=True,
        )),
    )
    rows = [{'cls': c, 'run': c.run, 'average': round(c.present / c.run, 1)} for c in classes if c.run]
    rows.sort(key=lambda r: (-r['average'], r['cls'].name))
    top = max((r['average'] for r in rows), default=0)
    for row in rows:
        row['percent'] = round(row['average'] / top * 100) if top else 0
    return {'class_rows': rows}


def payments_attention(board):
    """Automatic payments that failed, and saved cards that have expired or expire within 30 days."""
    from billing.autopay import needing_attention
    failed, expiring = needing_attention(board.org, board.today)
    expiring = list(expiring[:20])
    for autopay in expiring:
        autopay.state = autopay.expiry_state(board.today)
    return {'failed_payments': list(failed[:20]), 'failed_count': failed.count(),
            'expiring_cards': expiring, 'expiring_count': len(expiring)}


def recent_payments(board):
    from billing.models import Payment
    payments = Payment.objects.filter(invoice__organisation=board.org).select_related('invoice__member')
    return {'payments': list(payments.order_by('-paid_at')[:10])}


# ── Charts ───────────────────────────────────────────────────────────────

def attendance_trend(board):
    """Members present each week for the last 12 weeks, Monday to Sunday."""
    from classes.models import Attendance
    this_monday = board.today - timedelta(days=board.today.weekday())
    weeks = [this_monday - timedelta(weeks=n) for n in range(11, -1, -1)]
    counts = dict.fromkeys(weeks, 0)
    for day, n in Attendance.objects.filter(
        session__assigned_class_id__in=board.class_ids, present=True,
        session__date__gte=weeks[0], session__date__lte=board.today,
    ).values_list('session__date').annotate(n=Count('pk')):
        counts[day - timedelta(days=day.weekday())] += n
    return {'attendance_chart': _chart(
        'bar', [f'{w:%d %b}' for w in weeks], [{'label': 'Present', 'data': [counts[w] for w in weeks]}],
    )}


def member_growth(board):
    """Members joining each month for the last 12 months, and the active total at each month's end."""
    months = board.months_back(12)
    members = board.org.club_members
    joined = dict.fromkeys(months, 0)
    for month, n in members.filter(joined_date__gte=months[0], joined_date__lte=board.today).annotate(
        month=TruncMonth('joined_date'),
    ).values_list('month').annotate(n=Count('pk')):
        joined[_month(month)] += n
    return {'growth_chart': _chart(
        'bar', [f'{m:%b %y}' for m in months], [{'label': 'Joined', 'data': [joined[m] for m in months]}],
    ), 'joined_year': sum(joined.values())}


def revenue_trend(board):
    from billing.models import Payment
    months = board.months_back(12)
    totals = dict.fromkeys(months, Decimal(0))
    for month, total in Payment.objects.filter(
        invoice__organisation=board.org, paid_at__date__gte=months[0],
    ).annotate(month=TruncMonth('paid_at')).values_list('month').annotate(t=Sum('amount')):
        key = _month(month)
        if key in totals:
            totals[key] += total or 0
    return {'revenue_chart': _chart(
        'line', [f'{m:%b %y}' for m in months],
        [{'label': 'Payments', 'data': [float(totals[m]) for m in months]}], prefix='£',
    ), 'revenue_year': sum(totals.values())}


WIDGETS = (
    Widget('active_members', 'Active members', 'How many members are active, and how many joined this month.',
           'bi-people', 'numbers', (6, 3), (3, 2), active_members, min_px=200, max_size=(COLUMNS, 6)),
    Widget('sessions_week', 'Sessions this week', 'Sessions in the next 7 days and attendance over the last 4 weeks.',
           'bi-calendar-check', 'numbers', (6, 3), (3, 2), sessions_week, min_px=200, max_size=(COLUMNS, 6)),
    Widget('revenue_month', 'Revenue this month', 'Payments taken this month and what is still unpaid.',
           'bi-currency-pound', 'numbers', (6, 3), (3, 2), revenue_month, min_px=200, max_size=(COLUMNS, 6),
           admin_only=True, features=('billing',)),
    Widget('at_risk', 'Members at risk', 'Members not seen for 2 weeks, coming less often, or who have never come.',
           'bi-exclamation-triangle', 'numbers', (6, 3), (3, 2), at_risk, min_px=200, max_size=(COLUMNS, 6), admin_only=True),
    Widget('outstanding_invoices', 'Unpaid invoices', 'Unpaid invoices, and how many are overdue.',
           'bi-receipt', 'numbers', (6, 3), (3, 2), outstanding_invoices, min_px=200, max_size=(COLUMNS, 6),
           admin_only=True, features=('billing',)),
    Widget('attendance_rate', 'Attendance rate', 'Attendance over the last 4 weeks against the 4 weeks before.',
           'bi-graph-up-arrow', 'numbers', (6, 3), (3, 2), attendance_rate, min_px=200, max_size=(COLUMNS, 6)),
    Widget('new_members', 'New members', 'Members who joined this month against last month.',
           'bi-person-plus', 'numbers', (6, 3), (3, 2), new_members, min_px=200, max_size=(COLUMNS, 6), admin_only=True),
    Widget('club_rating', 'Club rating', 'Your average rating from members on the Dojo network.',
           'bi-star', 'numbers', (6, 3), (3, 2), club_rating, min_px=200, max_size=(COLUMNS, 6),
           needs_apps=('network',)),

    Widget('needs_attention', 'Needs attention', 'Applications, waivers, licences and staff checks that need action.',
           'bi-bell', 'lists', (24, 5), (6, 2), needs_attention, min_px=360, admin_only=True),
    Widget('upcoming_sessions', 'Upcoming sessions', 'The next 7 days of sessions, with a button to take each register.',
           'bi-clipboard-check', 'lists', (16, 8), (6, 4), upcoming_sessions, min_px=440),
    Widget('quick_actions', 'Quick actions', 'Shortcuts to the things you do most.',
           'bi-lightning', 'lists', (8, 8), (4, 3), quick_actions, min_px=220),
    Widget('not_seen_list', 'Not seen lately', 'The members behind "Members at risk", with when each last trained.',
           'bi-person-dash', 'lists', (10, 8), (5, 4), not_seen_list, min_px=280, admin_only=True),
    Widget('applications_list', 'Applications', 'Membership applications waiting for review.',
           'bi-inbox', 'lists', (10, 6), (5, 3), applications_list, min_px=260, admin_only=True, features=('applications',)),
    Widget('upcoming_gradings', 'Upcoming gradings', 'Grading sessions coming up, with how many are taking part.',
           'bi-award', 'lists', (10, 6), (5, 3), upcoming_gradings, min_px=300, capability='manage_gradings',
           features=('gradings',)),
    Widget('birthdays', 'Birthdays', 'Members with a birthday in the next two weeks.',
           'bi-cake2', 'lists', (8, 6), (4, 3), birthdays, min_px=240, admin_only=True),
    Widget('recent_reviews', 'Latest reviews', 'The newest reviews of your club on the Dojo network.',
           'bi-chat-square-quote', 'lists', (10, 7), (5, 4), recent_reviews, min_px=280,
           needs_apps=('network',)),
    Widget('class_attendance', 'Busiest classes', 'Average members at each class over the last 4 weeks.',
           'bi-bar-chart-steps', 'lists', (10, 7), (5, 4), class_attendance, min_px=260),
    Widget('payments_attention', 'Payments needing attention',
           'Automatic payments that failed, and members\' saved cards that expire soon.',
           'bi-credit-card', 'lists', (10, 7), (5, 4), payments_attention, min_px=300,
           admin_only=True, features=('billing',)),
    Widget('recent_payments', 'Recent payments', 'The latest payments recorded.',
           'bi-cash-coin', 'lists', (10, 7), (5, 4), recent_payments, min_px=280, admin_only=True, features=('billing',)),

    Widget('attendance_trend', 'Attendance trend', 'Members present each week for the last 12 weeks.',
           'bi-bar-chart', 'charts', (12, 7), (6, 5), attendance_trend, min_px=320),
    Widget('member_growth', 'New members by month', 'Members joining each month for the last year.',
           'bi-graph-up', 'charts', (12, 7), (6, 5), member_growth, min_px=320, admin_only=True),
    Widget('revenue_trend', 'Revenue by month', 'Payments taken each month for the last year.',
           'bi-cash-stack', 'charts', (12, 7), (6, 5), revenue_trend, min_px=320, admin_only=True, features=('billing',)),
)
WIDGETS_BY_KEY = {w.key: w for w in WIDGETS}

# The dashboard a club starts with: the same things the fixed dashboard used to show.
DEFAULT_WIDGETS = ('active_members', 'sessions_week', 'revenue_month', 'at_risk',
                   'needs_attention', 'upcoming_sessions', 'quick_actions')


# ── Layout ───────────────────────────────────────────────────────────────

def overlaps(a, b):
    return a['x'] < b['x'] + b['w'] and b['x'] < a['x'] + a['w'] \
        and a['y'] < b['y'] + b['h'] and b['y'] < a['y'] + a['h']


def first_fit(placed, w, h, columns):
    """The highest, then leftmost, free spot for a w×h widget."""
    columns = max(columns, w)
    for y in range(MAX_ROWS):
        for x in range(columns - w + 1):
            spot = {'x': x, 'y': y, 'w': w, 'h': h}
            if not any(overlaps(spot, p) for p in placed):
                return x, y
    return 0, max((p['y'] + p['h'] for p in placed), default=0)


def push_down(items):
    """Moves widgets down until none overlap, keeping the ones higher up where they are."""
    placed = []
    for item in sorted(items, key=lambda i: (i['y'], i['x'])):
        moved = True
        while moved:
            moved = False
            for p in placed:
                if overlaps(item, p):
                    item['y'] = p['y'] + p['h']
                    moved = True
        placed.append(item)
    return placed


def compact(items):
    """Moves each widget up as far as it can go, so nothing floats above a gap."""
    placed = []
    for item in sorted(items, key=lambda i: (i['y'], i['x'])):
        while item['y'] > 0 and not any(overlaps(dict(item, y=item['y'] - 1), p) for p in placed):
            item['y'] -= 1
        placed.append(item)
    return placed


def default_layout(org):
    """The standard layout as an admin sees it, packed around the widgets the club can't use."""
    placed = []
    for key in DEFAULT_WIDGETS:
        widget = WIDGETS_BY_KEY[key]
        if not widget.available(org):
            continue
        w, h = widget.size
        x, y = first_fit(placed, w, h, COLUMNS)
        placed.append({'key': key, 'x': x, 'y': y, 'w': w, 'h': h})
    return placed


def clean(raw, board, size='wide'):
    """A layout made safe to keep: known widgets this person can see, each once, in range, not overlapping."""
    columns = SIZE_COLUMNS[size]
    items, seen = [], set()
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        widget = WIDGETS_BY_KEY.get(entry.get('key'))
        if widget is None or widget.key in seen or not widget.visible(board):
            continue
        try:
            x, y, w, h = (int(entry.get(k, 0)) for k in ('x', 'y', 'w', 'h'))
        except (TypeError, ValueError):
            continue
        w, h = widget.clamp(w, h, size)
        items.append({'key': widget.key, 'x': max(0, min(x, columns - w)),
                      'y': max(0, min(y, MAX_ROWS)), 'w': w, 'h': h})
        seen.add(widget.key)
    return push_down(items)


def saved_layout(org):
    stored = (org.settings or {}).get(SETTINGS_KEY)
    if not isinstance(stored, dict) or not isinstance(stored.get('widgets'), list):
        return None
    if stored.get('version', 1) < 2:
        return upgrade(stored['widgets'])
    return stored['widgets']


def saved_tablet_layout(org):
    """The tablet arrangement someone made on a tablet, or None while it follows the wide one."""
    stored = (org.settings or {}).get(SETTINGS_KEY)
    if isinstance(stored, dict) and isinstance(stored.get('tablet'), list):
        return stored['tablet']
    return None


def upgrade(widgets):
    """A version 1 layout (fixed 50px cells) stretched onto the 24 columns, keeping each widget's
    place and share of the width. Widgets that sat side by side still do."""
    def number(entry, key):
        try:
            return int(entry.get(key, 0))
        except (TypeError, ValueError):
            return 0

    entries = [e for e in widgets if isinstance(e, dict)]
    right = max((number(e, 'x') + number(e, 'w') for e in entries), default=0)
    scale = COLUMNS / max(V1_COLUMNS, right)
    upgraded = []
    for entry in entries:
        x, w = number(entry, 'x'), number(entry, 'w')
        left, end = round(x * scale), round((x + w) * scale)
        upgraded.append({**entry, 'x': left, 'w': max(1, end - left)})
    return upgraded


def layout_for(board):
    """The wide layout this person sees: the club's (or the default), less the widgets they can't see."""
    raw = saved_layout(board.org)
    if raw is None:
        raw = default_layout(board.org)
    items = clean(raw, board)
    if len(items) < len(raw):
        items = compact(items)      # close the gaps left by widgets this person can't see
    return items


def tablet_from_wide(wide):
    """
    A tablet arrangement of a wide layout. Widgets keep their order. Each takes half its wide width
    (at least its tablet minimum). Widgets that started on the same row stay side by side if they
    fit in 12 columns; if not, they share the width evenly, as many to a row as fit.
    """
    runs = []
    for item in sorted(wide, key=lambda i: (i['y'], i['x'])):
        if runs and runs[-1][0]['y'] == item['y']:
            runs[-1].append(item)
        else:
            runs.append([item])
    placed, cursor = [], 0
    for run in runs:
        widgets = [WIDGETS_BY_KEY[i['key']] for i in run]
        want = [w.clamp(math.ceil(i['w'] / 2), i['h'], 'tablet')[0] for w, i in zip(widgets, run)]
        per_row = 0
        if sum(want) > TABLET_COLUMNS:
            most = max(1, min(TABLET_COLUMNS // w.min_w('tablet') for w in widgets))
            per_row = math.ceil(len(run) / math.ceil(len(run) / most))
        for n, (item, w) in enumerate(zip(run, want)):
            if per_row:
                base = TABLET_COLUMNS // per_row
                w = base + (1 if n % per_row < TABLET_COLUMNS - base * per_row else 0)
                prefer = 0
            else:
                prefer = min(round(item['x'] / 2), TABLET_COLUMNS - w)
            x, y = _spot(placed, w, item['h'], cursor, prefer, TABLET_COLUMNS)
            placed.append({'key': item['key'], 'x': x, 'y': y, 'w': w, 'h': item['h']})
            cursor = y
    return placed


def _spot(placed, w, h, from_y, prefer_x, columns):
    """The highest free spot at or below row from_y, trying column prefer_x first."""
    for y in range(from_y, MAX_ROWS):
        for x in [prefer_x, *range(columns - w + 1)]:
            if not any(overlaps({'x': x, 'y': y, 'w': w, 'h': h}, p) for p in placed):
                return x, y
    return 0, from_y


def fit_tablet(raw, wide, board):
    """A saved tablet layout matched to the wide one's widgets: any it lacks go underneath."""
    keys = {i['key'] for i in wide}
    items = [i for i in clean(raw, board, 'tablet') if i['key'] in keys]
    if len(items) < len([e for e in raw if isinstance(e, dict)]):
        items = compact(items)
    have = {i['key'] for i in items}
    for item in sorted(wide, key=lambda i: (i['y'], i['x'])):
        if item['key'] not in have:
            widget = WIDGETS_BY_KEY[item['key']]
            w, h = widget.clamp(math.ceil(item['w'] / 2), item['h'], 'tablet')
            x, y = first_fit(items, w, h, TABLET_COLUMNS)
            items.append({'key': item['key'], 'x': x, 'y': y, 'w': w, 'h': h})
    return items


def layouts_for(board):
    """{'wide': [...], 'tablet': [...], 'tablet_custom': bool} for this person."""
    wide = layout_for(board)
    raw = saved_tablet_layout(board.org)
    if raw is None:
        return {'wide': wide, 'tablet': tablet_from_wide(wide), 'tablet_custom': False}
    return {'wide': wide, 'tablet': fit_tablet(raw, wide, board), 'tablet_custom': True}


def save_layout(org, raw, board, tablet=None):
    """Saves the wide layout and, if given, the tablet one (None: the tablet follows the wide layout)."""
    items = clean(raw, board)
    stored = {'version': LAYOUT_VERSION, 'widgets': items}
    if isinstance(tablet, list):
        stored['tablet'] = fit_tablet(tablet, items, board)
    org.settings = {**(org.settings or {}), SETTINGS_KEY: stored}
    org.save(update_fields=['settings'])
    return items


def reset_layout(org):
    org.settings = {k: v for k, v in (org.settings or {}).items() if k != SETTINGS_KEY}
    org.save(update_fields=['settings'])


def catalogue(board):
    """Every widget this person could add, for the Add widget panel."""
    return [w.catalogue_entry() for w in WIDGETS if w.visible(board)]


def render_data(widget, board):
    return widget.data(board) if widget.data else {}


def grid_area(item):
    return f"{item['y'] + 1} / {item['x'] + 1} / span {item['h']} / span {item['w']}"
