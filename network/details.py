"""
A member's details, kept once in their Dojo account ("My details") and shared with clubs.

- Joining a club: its application form starts filled in from the account, showing only the fields
  that club collects (application_initial). Anything changed on the form is saved back to the
  account for next time (save_from_application).
- Clubs they've joined: the account owns the shared details of the account holder's own club
  records (Member.account_role == holder) and keeps each club's copy up to date (sync), each club
  getting only the fields it records: their name (first name and surname), preferred name,
  pronouns and date of birth included. Club staff see those fields read-only. Guardian rows linked to a parent's account follow its name, email and phone.

Each club keeps its own copy, rather than reading from the account, because each club is its own
data controller: it keeps records after someone leaves, and can delete them, without touching the
member's account or their other clubs.
"""
from datetime import date

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

# Every detail an account keeps, and the club setting (organisations/optional_fields.py) that
# decides whether a club collects it; None means every club does.
FIELDS = {
    'preferred_name': None,
    'pronouns': None,
    'date_of_birth': 'date_of_birth',
    'phone': 'phone',
    'address_line1': 'address',
    'address_line2': 'address',
    'city': 'address',
    'county': 'address',
    'postcode': 'address',
    'emergency_contact_name': None,
    'emergency_contact_phone': None,
    'emergency_contact_2_name': 'emergency_contact_2',
    'emergency_contact_2_phone': 'emergency_contact_2',
    'medical_info': 'medical_info',
    'guardian_name': 'guardians',
    'guardian_email': 'guardians',
    'guardian_phone': 'guardians',
}
# Details that also live on a club's member record (guardians are separate records there, and
# name and date of birth are kept up to date by each club).
MEMBER_FIELDS = [f for f in FIELDS if f not in ('date_of_birth', 'guardian_name', 'guardian_email', 'guardian_phone')]

LABELS = {
    'preferred_name': 'Preferred name', 'pronouns': 'Pronouns',
    'date_of_birth': 'Date of birth', 'phone': 'Phone',
    'address_line1': 'Address', 'address_line2': 'Second line', 'city': 'Town or city', 'county': 'County',
    'postcode': 'Postcode',
    'emergency_contact_name': 'Name', 'emergency_contact_phone': 'Phone',
    'emergency_contact_2_name': 'Second contact: name', 'emergency_contact_2_phone': 'Second contact: phone',
    'medical_info': 'Conditions, allergies, or anything coaches should know',
    'guardian_name': 'Name', 'guardian_email': 'Email', 'guardian_phone': 'Phone',
}
PLACEHOLDERS = {'preferred_name': 'If not your first name', 'pronouns': 'e.g. she/her, he/him, they/them'}
INPUT_TYPES = {'date_of_birth': 'date', 'phone': 'tel', 'emergency_contact_phone': 'tel',
               'emergency_contact_2_phone': 'tel', 'guardian_phone': 'tel', 'guardian_email': 'email'}
AUTOCOMPLETE = {'date_of_birth': 'bday', 'phone': 'tel', 'address_line1': 'address-line1',
                'address_line2': 'address-line2', 'city': 'address-level2', 'county': 'address-level1',
                'postcode': 'postal-code'}
NOTES = {
    'About you': 'Your preferred name and pronouns show on class registers as [preferred name] [surname] (pronouns).',
    'Parent or guardian': 'Only needed if you\'re under 18. Clubs that look after juniors ask for it.',
    'Medical information': 'Clubs that ask for it show it to coaches on their registers. Leave blank if there\'s nothing.',
}

GROUPS = [
    ('About you', ['preferred_name', 'pronouns', 'date_of_birth', 'phone']),
    ('Address', ['address_line1', 'address_line2', 'city', 'county', 'postcode']),
    ('Emergency contacts', ['emergency_contact_name', 'emergency_contact_phone',
                            'emergency_contact_2_name', 'emergency_contact_2_phone']),
    ('Medical information', ['medical_info']),
    ('Parent or guardian', ['guardian_name', 'guardian_email', 'guardian_phone']),
]


def club_collects(org, field):
    switch = FIELDS[field]
    return switch is None or org.has_field(switch)


def application_initial(account):
    """What a club's join form starts with for this account (the form shows only what the club collects)."""
    values = {'name': account.name, 'first_name': account.first_name, 'surname': account.surname,
              'email': account.email}
    for field in FIELDS:
        value = getattr(account, field)
        values[field] = value.isoformat() if isinstance(value, date) else value
    return values


def clean(data, fields=FIELDS):
    """({field: value} for the given fields from submitted data, errors). Blank values stay blank."""
    values, errors = {}, []
    for field in fields:
        raw = data.get(field, '')
        raw = raw.strip() if isinstance(raw, str) else ''
        if field == 'date_of_birth':
            if raw:
                try:
                    values[field] = date.fromisoformat(raw)
                except ValueError:
                    errors.append('Enter the date of birth as a date.')
            else:
                values[field] = None
            continue
        if field == 'guardian_email' and raw:
            try:
                validate_email(raw)
            except ValidationError:
                errors.append('Enter a valid email for the parent or guardian.')
                continue
        values[field] = raw
    return values, errors


def save_from_application(account, org, data):
    """
    After applying to `org`, keep what was entered for next time: the fields this club collects
    that were filled in. Blank fields don't wipe what the account already has. Returns the fields changed.
    """
    values, _ = clean(data, [f for f in FIELDS if club_collects(org, f)])
    for part in ('first_name', 'surname'):
        values[part] = (data.get(part) or '').strip()[:150]
    changed = []
    for field, value in values.items():
        if value not in ('', None) and getattr(account, field) != value:
            setattr(account, field, value)
            changed.append(field)
    if 'first_name' in changed or 'surname' in changed:
        changed.append('name')
    if changed:
        account.save(update_fields=changed)
    return changed


# Details the account owns on the account holder's club records; club staff see them read-only.
SHARED_MEMBER_FIELDS = ['email', 'first_name', 'surname', 'date_of_birth'] + MEMBER_FIELDS
ALWAYS_SHARED = {'email', 'first_name', 'surname'}   # every club records these
# What a guardian row linked to a parent's account takes from it.
GUARDIAN_FIELDS = {'name': 'name', 'email': 'email', 'phone': 'phone'}


def _blank(value):
    return value in ('', None)


def own_memberships(account):
    """The account holder's own current member records (not, say, their children's, which share the account)."""
    from members.models import Member
    return (account.memberships.filter(is_active=True, account_role=Member.AccountRole.HOLDER)
            .select_related('organisation'))


def sync(account, changed=()):
    """
    Bring the account holder's club records, and guardian rows linked to the account, up to date
    with it. Each club gets only the fields it records. A value the account doesn't have is only
    copied (as blank) when the member has just cleared it (`changed`), so a blank never wipes what a
    club holds by accident. Returns the clubs updated, by name.
    """
    changed = set(changed)
    updated = set()

    def wanted(value, field):
        return not _blank(value) or field in changed

    for member in own_memberships(account):
        org = member.organisation
        fields = []
        for field in SHARED_MEMBER_FIELDS:
            value = getattr(account, field)
            if field not in ALWAYS_SHARED and not club_collects(org, field):
                continue
            empty = None if field == 'date_of_birth' else ''
            if wanted(value, field) and getattr(member, field) != (value or empty):
                setattr(member, field, value or empty)
                fields.append(field)
        if fields:
            member.save(update_fields=fields)
            updated.add(org)
    for guardian in account.guardian_of.filter(member__is_active=True).select_related('member__organisation'):
        fields = []
        for field, source in GUARDIAN_FIELDS.items():
            value = getattr(account, source)
            if wanted(value, source) and getattr(guardian, field) != (value or ''):
                setattr(guardian, field, (value or '')[:255])
                fields.append(field)
        if fields:
            guardian.save(update_fields=fields)
            updated.add(guardian.member.organisation)
    return sorted(updated, key=lambda org: org.name)


def adopt(account, members):
    """
    When club records are first linked to an account: fill in what the account is missing from
    them, where they all agree. Nothing the account already has, or any club holds, is changed.
    Returns the fields filled in.
    """
    filled = []
    for field in ['date_of_birth'] + MEMBER_FIELDS:
        if not _blank(getattr(account, field)):
            continue
        values = {getattr(m, field) for m in members} - {'', None}
        if isinstance(next(iter(values), None), str):
            values = {v.strip() for v in values} - {''}
        if len(values) == 1:
            setattr(account, field, values.pop())
            filled.append(field)
    if filled:
        account.save(update_fields=filled)
    return filled


def form_groups(account, data=None):
    """My details laid out for the template: [{'title', 'note', 'fields': [{name, label, type, value, ...}]}]."""
    groups = []
    for title, names in GROUPS:
        fields = []
        for name in names:
            if data is not None:
                value = data.get(name, '')
            else:
                value = getattr(account, name)
                value = value.isoformat() if isinstance(value, date) else (value or '')
            fields.append({'name': name, 'label': LABELS[name], 'type': INPUT_TYPES.get(name, 'text'),
                           'placeholder': PLACEHOLDERS.get(name, ''), 'list': 'pronoun-options' if name == 'pronouns' else '',
                           'autocomplete': AUTOCOMPLETE.get(name, 'off'), 'value': value,
                           'wide': name in ('address_line1', 'address_line2', 'medical_info')})
        groups.append({'title': title, 'note': NOTES.get(title, ''), 'fields': fields})
    return groups
