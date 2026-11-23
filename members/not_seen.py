"""
"Not seen lately": active members a club may be about to lose, from their attendance.

- Not seen: they used to come, but haven't been marked present for `days` days or more.
- Never come: no attendance at all, and they joined at least `days` days ago (or the club didn't
  record when they joined).
- Coming less often: still coming, but in the last 4 weeks they've come half as often as they
  usually do (their average over the 12 weeks before), having usually come at least twice a month.
"""
from datetime import timedelta

from django.db.models import Count, Max, Q
from django.utils import timezone

DAY_CHOICES = (14, 21, 30, 60, 90)
DEFAULT_DAYS = 21
RECENT_DAYS = 28
USUAL_DAYS = 84
MIN_USUAL = 2   # sessions in a usual 4 weeks before a drop counts


def _rows(org, today):
    from classes.models import Attendance
    from .models import Member
    members = list(Member.objects.filter(organisation=org, is_active=True, archived_at__isnull=True)
                   .prefetch_related('enrolments__assigned_class', 'leaving_requests', 'guardians')
                   .select_related('billing_policy'))
    recent_from = today - timedelta(days=RECENT_DAYS)
    usual_from = recent_from - timedelta(days=USUAL_DAYS)
    stats = {row['member_id']: row for row in Attendance.objects.filter(member__in=members, present=True).values('member_id').annotate(
        last=Max('session__date'),
        recent=Count('pk', filter=Q(session__date__gt=recent_from, session__date__lte=today)),
        before=Count('pk', filter=Q(session__date__gt=usual_from, session__date__lte=recent_from)),
    )}
    for member in members:
        row = stats.get(member.pk, {})
        last = row.get('last')
        yield {
            'member': member,
            'last_seen': last,
            'days_since': (today - last).days if last else None,
            'recent': row.get('recent', 0),
            'usual': round(row.get('before', 0) * RECENT_DAYS / USUAL_DAYS, 1),
        }


def report(org, days=DEFAULT_DAYS, today=None, describe=True):
    """{'not_seen', 'never', 'slowing'}: lists of rows (member, last_seen, days_since, recent, usual).
    With `describe`, each member also gets class_names, plans, leaving and overdue for the page."""
    today = today or timezone.localdate()
    cutoff = today - timedelta(days=days)
    result = {'not_seen': [], 'never': [], 'slowing': []}
    for row in _rows(org, today):
        member = row['member']
        if row['last_seen'] is None:
            if member.joined_date is None or member.joined_date <= cutoff:
                result['never'].append(row)
        elif row['last_seen'] <= cutoff:
            result['not_seen'].append(row)
        elif row['usual'] >= MIN_USUAL and row['recent'] <= row['usual'] / 2:
            result['slowing'].append(row)
    result['not_seen'].sort(key=lambda r: r['last_seen'])
    result['never'].sort(key=lambda r: (r['member'].joined_date or today, r['member'].name))
    result['slowing'].sort(key=lambda r: (r['recent'] - r['usual'], r['member'].name))
    if describe:
        for rows in result.values():
            for row in rows:
                _describe(row['member'])
    return result


def _describe(member):
    """What the report shows alongside each member: classes, plan, leaving request, overdue invoices."""
    from .models import MemberLeavingRequest, current_plans
    member.class_names = sorted({cm.assigned_class.name for cm in member.enrolments.all()})
    member.plans = current_plans(member)
    member.leaving = next((r for r in member.leaving_requests.all()
                           if r.status == MemberLeavingRequest.Status.PENDING), None)
    member.overdue = member.invoices.filter(
        Q(status='overdue') | Q(status='unpaid', due_date__lt=timezone.localdate())).count()

