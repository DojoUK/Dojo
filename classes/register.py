"""
What a register shows about each person beyond their name: badges from
custom fields the club has put on registers, staff emergency contacts, and
members who have left the class but were marked for the session.
"""
from members.models import CustomField, Member

from .models import Attendance, ClassMember


def register_flag_fields(org):
    """The club's yes/no custom fields switched on for registers."""
    return list(CustomField.objects.filter(
        organisation=org, field_type=CustomField.FieldType.BOOLEAN, show_on_registers=True,
    ))


def attach_register_flags(org, members, fields=None):
    """
    Set `.register_flags` on each member: the names of the register yes/no
    fields ticked for them (e.g. "Photo consent"). Returns the members.
    """
    fields = register_flag_fields(org) if fields is None else fields
    for member in members:
        values = member.custom_field_values or {}
        member.register_flags = [f.name for f in fields if _is_yes(values.get(str(f.pk)))]
    return members


def _is_yes(value):
    """A ticked yes/no value: True from the member form, or yes-like text from an import."""
    if isinstance(value, str):
        return value.strip().lower() in ('yes', 'y', 'true', '1')
    return value is True


def with_former_members(assigned_class, session, enrolments):
    """
    The class's enrolments plus anyone marked for this session who has since
    left the class, as unsaved ClassMember rows with `.is_former_member`
    set, sorted by name. Attendance belongs to the member, not the
    enrolment, so a past register keeps showing who was actually there.
    """
    enrolments = list(enrolments)
    for cm in enrolments:
        cm.is_former_member = False
    current_ids = {cm.member_id for cm in enrolments}
    former_ids = (
        Attendance.objects.filter(session=session)
        .exclude(member_id__in=current_ids)
        .values_list('member_id', flat=True)
    )
    former = Member.objects.filter(pk__in=list(former_ids), organisation=assigned_class.organisation)
    if not former:
        return enrolments
    for member in former:
        ghost = ClassMember(assigned_class=assigned_class, member=member)
        ghost.is_former_member = True
        enrolments.append(ghost)
    enrolments.sort(key=lambda cm: (cm.member.name or '').lower())
    return enrolments


def attach_staff_contacts(org, class_coaches):
    """
    Set `.contacts` on each ClassCoach: [(name, phone)] for the staff
    member's emergency contacts the club collects. Returns a list.
    """
    from organisations.models import OrganisationMember

    class_coaches = list(class_coaches)
    show_first = org.has_field('staff_emergency_contact')
    show_second = org.has_field('staff_emergency_contact_2')
    by_user = {
        om.user_id: om
        for om in OrganisationMember.objects.filter(
            organisation=org, user_id__in=[cc.user_id for cc in class_coaches],
        )
    }
    for cc in class_coaches:
        om = by_user.get(cc.user_id)
        contacts = []
        if om and show_first and (om.emergency_contact_name or om.emergency_contact_phone):
            contacts.append((om.emergency_contact_name, om.emergency_contact_phone))
        if om and show_second and (om.emergency_contact_2_name or om.emergency_contact_2_phone):
            contacts.append((om.emergency_contact_2_name, om.emergency_contact_2_phone))
        cc.contacts = contacts
    return class_coaches


def member_contacts(org, member):
    """[(name, phone)] for a member's emergency contacts the club collects."""
    contacts = []
    if member.emergency_contact_name or member.emergency_contact_phone:
        contacts.append((member.emergency_contact_name, member.emergency_contact_phone))
    if org.has_field('emergency_contact_2') and (member.emergency_contact_2_name or member.emergency_contact_2_phone):
        contacts.append((member.emergency_contact_2_name, member.emergency_contact_2_phone))
    return contacts


def attach_member_contacts(org, members):
    """Set `.contacts` ([(name, phone)]) on each member. Returns the members."""
    for member in members:
        member.contacts = member_contacts(org, member)
    return members
