"""
Families: members linked in a FamilyGroup share one member portal (a switcher between them),
see each other's invoices together, and can share one family billing plan.
"""
from .models import FamilyGroup, FamilyGroupMember, Member


def family_group(member):
    """The member's family group (their first, if staff somehow put them in several), or None."""
    link = (FamilyGroupMember.objects.filter(member=member).select_related('family_group')
            .order_by('pk').first())
    return link.family_group if link else None


def family_members(member, active_only=True):
    """Everyone in the member's family, including the member, by name. Just [member] if they have no family."""
    group = family_group(member)
    if group is None:
        return [member]
    members = Member.objects.filter(family_memberships__family_group=group).select_related('organisation')
    if active_only:
        members = members.filter(is_active=True)
    members = list(members.distinct().order_by('name'))
    if all(m.pk != member.pk for m in members):
        members.insert(0, member)
    return members


def siblings(member):
    return [m for m in family_members(member) if m.pk != member.pk]


class FamilyPlanFull(Exception):
    """A family plan can't cover this many family members; the message says why."""


def plan_room_error(member, policy, count=None):
    """
    An error message if putting `member` on the family plan `policy` would spread it over more family
    members than its limit, else ''. `count` is how many members would be on it (default: the member
    plus any siblings already on it).
    """
    if policy is None or not policy.is_family_plan or not policy.max_family_members:
        return ''
    if count is None:
        count = 1 + sum(1 for s in siblings(member) if s.billing_policy_id == policy.pk)
    if count > policy.max_family_members:
        return (f'{policy.name} covers up to {policy.max_family_members} family members, '
                f'so it can\'t be spread over {count}.')
    return ''


def assign_plan(member, policy):
    """
    Put `member` on `policy` (or off any plan if None) and return the members changed. A family plan
    covers the whole family, so it is put on every active member of the family at once; raises
    FamilyPlanFull, changing nothing, if the plan has a limit that the family is over.
    """
    targets = family_members(member) if policy is not None and policy.is_family_plan else [member]
    error = plan_room_error(member, policy, count=len(targets))
    if error:
        raise FamilyPlanFull(error + ' Choose a different plan, or take someone out of the family.')
    for target in targets:
        if target.billing_policy_id != (policy.pk if policy else None):
            target.billing_policy = policy
            target.save(update_fields=['billing_policy'])
    return targets


def link(member, other, name=None):
    """Put `other` in `member`'s family, making the family first if `member` has none."""
    group = family_group(member)
    if group is None:
        surname = (member.name or '').split(' ')[-1] or 'Family'
        group = FamilyGroup.objects.create(organisation=member.organisation, name=name or f'{surname} family')
        FamilyGroupMember.objects.create(family_group=group, member=member)
    FamilyGroupMember.objects.get_or_create(family_group=group, member=other)
    return group


def unlink(member):
    """Take a member out of their family. A family left with fewer than two members is removed."""
    group = family_group(member)
    if group is None:
        return
    FamilyGroupMember.objects.filter(family_group=group, member=member).delete()
    if group.memberships.count() < 2:
        group.delete()
