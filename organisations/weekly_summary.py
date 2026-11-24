"""
The weekly club summary: an email on Monday mornings to club admins who switch it on in My settings,
with last week's attendance, members, money and the things to follow up, plus the "Not seen lately"
list as a CSV. The scheduler calls send_due() every half hour; each person gets one a week.
"""
import csv
import io
from datetime import datetime, time, timedelta

from django.conf import settings
from django.db.models import Q, Sum
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

SEND_FROM_HOUR = 7


def _week_text(start, end):
    if start.month == end.month:
        return f'{start.day}–{end.day} {end:%B %Y}'
    return f'{start.day} {start:%B} – {end.day} {end:%B %Y}'


def build(org, today=None):
    """The figures for the week before `today`'s week (Monday to Sunday)."""
    from billing.models import Invoice, Payment
    from classes.models import Attendance, Session
    from members import not_seen
    from members.models import Member, MemberApplication, MemberLeavingRequest

    from .models import OrganisationMember

    today = today or timezone.localdate()
    start = today - timedelta(days=today.weekday() + 7)
    end = start + timedelta(days=6)
    before = (start - timedelta(days=7), start - timedelta(days=1))

    def present(first, last):
        return Attendance.objects.filter(session__assigned_class__organisation=org, present=True,
                                         session__date__gte=first, session__date__lte=last).count()

    sessions = Session.objects.filter(assigned_class__organisation=org, is_cancelled=False,
                                      date__gte=start, date__lte=end)
    found = not_seen.report(org, describe=False, today=today)
    site = settings.SITE_URL.rstrip('/')
    summary = {
        'org': org, 'week': _week_text(start, end),
        'sessions': sessions.count(),
        'registers_missing': sessions.exclude(attendance__isnull=False).distinct().count(),
        'attended': present(start, end), 'attended_before': present(*before),
        'active_members': Member.objects.filter(organisation=org, is_active=True).count(),
        'new_members': Member.objects.filter(organisation=org, joined_date__gte=start, joined_date__lte=end).count(),
        'not_seen': len(found['not_seen']), 'slowing': len(found['slowing']), 'never': len(found['never']),
        'not_seen_names': [r['member'].name for r in (found['not_seen'] + found['slowing'])[:5]],
        'leaving': MemberLeavingRequest.objects.filter(member__organisation=org, status='pending').count(),
        'applications': (MemberApplication.objects.filter(organisation=org, status='pending').count()
                         if org.has_feature('applications') else None),
        'not_seen_url': site + reverse('member_not_seen', kwargs={'org_slug': org.slug}),
        'dashboard_url': site + reverse('org_dashboard', kwargs={'org_slug': org.slug}),
        'billing': org.has_feature('billing'),
        'staff_checks_expiring': OrganisationMember.objects.filter(organisation=org).filter(
            Q(dbs_expiry__gte=today, dbs_expiry__lte=today + timedelta(days=30))
            | Q(coaching_licence_expiry__gte=today, coaching_licence_expiry__lte=today + timedelta(days=30))).count(),
    }
    summary['attended_change'] = summary['attended'] - summary['attended_before']
    if summary['billing']:
        window = (timezone.make_aware(datetime.combine(start, time.min)),
                  timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min)))
        overdue = Invoice.objects.filter(organisation=org).filter(
            Q(status='overdue') | Q(status='unpaid', due_date__lt=today))
        from billing.autopay import needing_attention
        failed, expiring = needing_attention(org, today)
        summary.update(
            payments_failed=failed.count(),
            cards_expiring=expiring.count(),
            paid=Payment.objects.filter(invoice__organisation=org, paid_at__gte=window[0], paid_at__lt=window[1])
            .aggregate(total=Sum('amount'))['total'] or 0,
            overdue_count=overdue.count(),
            overdue_total=overdue.aggregate(total=Sum('amount'))['total'] or 0,
        )
    summary['found'] = found
    return summary


def summary_text(s):
    lines = [f"Sessions run: {s['sessions']}" + (f" ({s['registers_missing']} without a register)" if s['registers_missing'] else ''),
             f"Attendances: {s['attended']} ({s['attended_change']:+d} on the week before)",
             f"Active members: {s['active_members']} ({s['new_members']} new)",
             f"Not seen in 3 weeks: {s['not_seen']} · coming less often: {s['slowing']} · never come: {s['never']}"]
    if s['billing']:
        lines.append(f"Paid last week: £{s['paid']:.2f} · overdue: {s['overdue_count']} invoices, £{s['overdue_total']:.2f}")
    if s['applications']:
        lines.append(f"Applications waiting: {s['applications']}")
    if s.get('payments_failed'):
        lines.append(f"Automatic payments failed: {s['payments_failed']}")
    if s.get('cards_expiring'):
        lines.append(f"Saved cards expired or expiring in 30 days: {s['cards_expiring']}")
    if s['leaving']:
        lines.append(f"Members asking to leave: {s['leaving']}")
    if s['staff_checks_expiring']:
        lines.append(f"Staff checks expiring in 30 days: {s['staff_checks_expiring']}")
    lines.append(f"Not seen lately: {s['not_seen_url']}")
    return '\n'.join(lines)


def not_seen_csv(found):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['Group', 'Name', 'Last seen', 'Days since', 'Last 4 weeks', 'Usual 4 weeks'])
    labels = {'not_seen': 'Not seen', 'slowing': 'Coming less often', 'never': 'Never come'}
    for key, rows in found.items():
        for row in rows:
            writer.writerow([labels[key], row['member'].name, row['last_seen'] or '',
                             '' if row['days_since'] is None else row['days_since'], row['recent'], row['usual']])
    return out.getvalue()


def send_to(membership, summary):
    from mailer import compose
    user = membership.user
    first = user.first_name or user.username
    message = compose.send(
        membership.organisation, 'weekly_summary', [user.email],
        {'first_name': first, 'week': summary['week']},
        {'summary': {'html': render_to_string('emails/blocks/weekly_summary.html', summary), 'text': summary_text(summary)}},
        attachments=[('not-seen-lately.csv', not_seen_csv(summary['found']), 'text/csv')],
    )
    return message


def send_due(now=None):
    """Send this week's summary to everyone who wants it and hasn't had it. Returns how many were sent."""
    from .models import OrganisationMember
    now = timezone.localtime(now or timezone.now())
    today = now.date()
    monday = today - timedelta(days=today.weekday())
    if today == monday and now.hour < SEND_FROM_HOUR:
        return 0
    wanted = (OrganisationMember.objects.filter(weekly_summary=True, role='org_admin', user__is_active=True)
              .exclude(user__email='').filter(Q(weekly_summary_sent_on__isnull=True) | Q(weekly_summary_sent_on__lt=monday))
              .select_related('organisation', 'user'))
    summaries, sent = {}, 0
    for membership in wanted:
        org = membership.organisation
        if org.pk not in summaries:
            summaries[org.pk] = build(org, today)
        try:
            send_to(membership, summaries[org.pk])
        except Exception:
            continue
        membership.weekly_summary_sent_on = today
        membership.save(update_fields=['weekly_summary_sent_on'])
        sent += 1
    return sent
