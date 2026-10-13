"""
Grading rules kept out of the views: who is eligible, which grade each
candidate goes for by default, and how a result flows into the member's
grade history.
"""
from django.db import transaction

from classes.models import Attendance
from members.models import Member

from .models import GradingCandidate, MemberProgression


def current_progressions(system, member_ids, before_grading=None):
    """
    {member_id: latest MemberProgression in this system} for the given
    members. With `before_grading`, grades awarded by that grading are
    ignored, giving the grade each member held going into it.
    """
    progressions = MemberProgression.objects.filter(member_id__in=member_ids, stage__system=system)
    if before_grading is not None:
        progressions = progressions.exclude(grading_candidate__session__grading=before_grading)
    latest = {}
    for progression in (
        progressions
        .select_related('stage')
        .order_by('member_id', '-achieved_date', '-pk')
    ):
        latest.setdefault(progression.member_id, progression)
    return latest


def default_target(system, current):
    """The grade a candidate goes for: the one after their current grade, or the system's first."""
    if current is None:
        return system.stages.order_by('order', 'name').first()
    return current.stage.next_stage()


def eligibility_rows(session):
    """
    Active members who could be added to this session, with their current
    grade and how many sessions they've attended since it was awarded (or
    ever, if they have no grade yet). Members already in any session of
    this grading are left out. Most-attended first.
    """
    grading = session.grading
    taken = GradingCandidate.objects.filter(session__grading=grading).values('member_id')
    members = list(
        Member.objects.filter(organisation=grading.organisation, is_active=True)
        .exclude(pk__in=taken)
        .order_by('name')
    )
    current = current_progressions(grading.system, [m.pk for m in members])

    attended = {}
    for member_id, session_date in Attendance.objects.filter(
        member__in=members, present=True,
    ).values_list('member_id', 'session__date'):
        since = current.get(member_id)
        if since is None or session_date > since.achieved_date:
            attended[member_id] = attended.get(member_id, 0) + 1

    rows = [
        {'member': m, 'current': current.get(m.pk), 'attended_since': attended.get(m.pk, 0)}
        for m in members
    ]
    rows.sort(key=lambda r: -r['attended_since'])
    return rows


def add_candidates(session, members):
    """Add members to a session, each going for their next grade. Returns how many were added."""
    grading = session.grading
    already = set(
        GradingCandidate.objects.filter(session__grading=grading).values_list('member_id', flat=True)
    )
    members = [m for m in members if m.pk not in already]
    current = current_progressions(grading.system, [m.pk for m in members])
    GradingCandidate.objects.bulk_create([
        GradingCandidate(session=session, member=m, target_stage=default_target(grading.system, current.get(m.pk)))
        for m in members
    ])
    return len(members)


def progression_note(candidate):
    return f'Graded at {candidate.session.grading.name} ({candidate.session.display_name})'


@transaction.atomic
def record_result(candidate, result, target_stage, notes):
    """
    Store a candidate's result. A pass creates (or updates) the single grade
    history entry linked to this candidate; any other result removes it.
    """
    candidate.result = result
    candidate.target_stage = target_stage
    candidate.notes = notes

    if result == GradingCandidate.Result.PASSED:
        progression = candidate.progression
        if progression is None:
            progression = MemberProgression(member=candidate.member)
        progression.stage = target_stage
        progression.achieved_date = candidate.session.date
        progression.notes = progression_note(candidate)
        progression.save()
        candidate.progression = progression
    elif candidate.progression_id:
        candidate.progression.delete()
        candidate.progression = None

    candidate.save()


def move_session_date(session, new_date):
    """Change a session's date, keeping the grade history of its passes in step."""
    session.date = new_date
    session.save(update_fields=['date'])
    MemberProgression.objects.filter(grading_candidate__session=session).update(achieved_date=new_date)
