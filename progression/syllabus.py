"""Which syllabus each member is working on, and what they've had signed off."""
from .models import MemberProgression, MemberTechnique


def current_syllabuses(member):
    """
    [{'stage', 'syllabus', 'groups', 'done', 'total'}] for the syllabus
    linked to the member's current grade in each progression system,
    skipping grades with none. Each entry in `groups` carries
    `.signed_off`: the member's MemberTechnique for it, or None.
    """
    latest = {}
    for progression in (
        MemberProgression.objects.filter(member=member)
        .select_related('stage__system', 'stage__syllabus')
        .order_by('-achieved_date', '-pk')
    ):
        latest.setdefault(progression.stage.system_id, progression)
    signed_off = {
        mt.technique_id: mt
        for mt in MemberTechnique.objects.filter(member=member).select_related('signed_off_by')
    }
    cards = []
    for progression in latest.values():
        syllabus = progression.stage.syllabus
        if syllabus is None:
            continue
        groups = syllabus.grouped_entries()
        entries = [entry for _, group in groups for entry in group]
        for entry in entries:
            entry.signed_off = signed_off.get(entry.technique_id)
        cards.append({
            'stage': progression.stage, 'syllabus': syllabus, 'groups': groups,
            'done': sum(1 for e in entries if e.signed_off), 'total': len(entries),
        })
    return cards
