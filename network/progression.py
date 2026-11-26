"""
A member's progression, wherever they gained it: grades awarded by each club on Dojo (that club's
MemberProgression records, which only that club can change) and grades they've added themselves
(PriorProgression, which only they can change). It follows them from club to club: the member
sees it all as their Dojo Passport, and each club sees the rest of it read only.

Grouped by martial art: the one with the most recent grade first, then the next most recent, and
so on; within each, newest first.
"""
from datetime import date

from organisations.disciplines import LABELS

from .models import PriorProgression


def _label(key):
    return LABELS.get(key) or 'Other martial arts'


def club_entries(members, exclude_org=None):
    """Grades awarded by clubs on Dojo to these member records, with whether each was a promotion or demotion."""
    from progression.models import MemberProgression
    records = (MemberProgression.objects.filter(member__in=members)
               .select_related('stage__system__organisation', 'member')
               .order_by('member_id', 'stage__system_id', 'achieved_date', 'pk'))
    entries, last_order = [], {}
    for record in records:
        stage, system = record.stage, record.stage.system
        key = (record.member_id, system.pk)
        previous = last_order.get(key)
        change = ('awarded' if previous is None or stage.order == previous
                  else 'promotion' if stage.order > previous else 'demotion')
        last_order[key] = stage.order
        if exclude_org is not None and system.organisation_id == exclude_org.pk:
            continue
        entries.append({
            'source': 'club', 'id': record.pk, 'discipline': system.discipline or 'other',
            'grade': stage.name, 'colour': stage.colour, 'system': system.name, 'date': record.achieved_date,
            'awarded_by': system.organisation.name, 'org_id': system.organisation_id, 'change': change,
        })
    return entries


def prior_entries(account):
    return [{
        'source': 'self', 'id': p.pk, 'discipline': p.discipline or 'other', 'grade': p.grade, 'colour': '',
        'system': '', 'date': p.achieved_date, 'awarded_by': p.awarded_by, 'org_id': None, 'change': '',
        'record': p,
    } for p in PriorProgression.objects.filter(account=account)]


def grouped(entries):
    """[{'discipline', 'label', 'latest', 'entries'}], the martial art with the most recent grade first."""
    groups = {}
    for entry in entries:
        groups.setdefault(entry['discipline'], []).append(entry)
    result = []
    for key, items in groups.items():
        items.sort(key=lambda e: (e['date'] or date.min, e['source'] == 'club', e['id']), reverse=True)
        result.append({'discipline': key, 'label': _label(key), 'latest': items[0]['date'], 'entries': items})
    result.sort(key=lambda g: (g['latest'] or date.min, g['label']), reverse=True)
    return result


def _same_person(members, member):
    name = (member.name or '').strip().lower()
    return [m for m in members if (m.name or '').strip().lower() == name]


def people(account):
    """
    Everyone on the account with progression: the account holder first (their club grades and the
    ones they've added), then each person they look after (matched across clubs by name).
    [{'name', 'is_holder', 'groups'}]
    """
    from members.models import Member
    memberships = list(account.memberships.select_related('organisation'))
    holder = [m for m in memberships if m.account_role == Member.AccountRole.HOLDER]
    result = [{'name': account.name, 'is_holder': True,
               'groups': grouped(club_entries(holder) + prior_entries(account))}]
    seen = set()
    for member in memberships:
        if member.account_role != Member.AccountRole.DEPENDANT:
            continue
        key = (member.name or '').strip().lower()
        if key in seen:
            continue
        seen.add(key)
        same = _same_person([m for m in memberships if m.account_role == Member.AccountRole.DEPENDANT], member)
        groups = grouped(club_entries(same))
        if groups:
            result.append({'name': member.name, 'is_holder': False, 'groups': groups})
    return result


def elsewhere(member):
    """
    For a club: the member's progression from other clubs on Dojo and the grades they've added
    themselves, read only. Only for records linked to a Dojo account.
    """
    from members.models import Member
    account = member.account
    if not account:
        return []
    org = member.organisation
    if member.account_role == Member.AccountRole.HOLDER:
        others = account.memberships.filter(account_role=Member.AccountRole.HOLDER).exclude(organisation=org)
        return grouped(club_entries(others) + prior_entries(account))
    dependants = list(account.memberships.filter(account_role=Member.AccountRole.DEPENDANT).exclude(organisation=org))
    return grouped(club_entries(_same_person(dependants, member)))
