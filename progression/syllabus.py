"""Which syllabus each member is working on."""
from .models import MemberProgression


def current_syllabuses(member):
    """
    [{'stage', 'syllabus', 'groups'}] for the syllabus linked to the member's
    current grade in each progression system, skipping grades with none.
    """
    latest = {}
    for progression in (
        MemberProgression.objects.filter(member=member)
        .select_related('stage__system', 'stage__syllabus')
        .order_by('-achieved_date', '-pk')
    ):
        latest.setdefault(progression.stage.system_id, progression)
    cards = []
    for progression in latest.values():
        syllabus = progression.stage.syllabus
        if syllabus is not None:
            cards.append({'stage': progression.stage, 'syllabus': syllabus, 'groups': syllabus.grouped_entries()})
    return cards
