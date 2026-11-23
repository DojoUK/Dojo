"""
Link what's already there, without changing anything a club holds:

- Club records already linked to a Dojo account become the account holder's own record when their
  email is the account's (at most one per club), or a dependant's otherwise.
- Guardian rows on those records that use the account's email are linked to the account.
- Details the account doesn't have yet are filled in from the account holder's club records, but
  only where all their clubs agree. Club records are only updated the next time the member saves.
"""
from django.db import migrations

DETAILS = [
    'date_of_birth', 'phone', 'address_line1', 'address_line2', 'city', 'county', 'postcode',
    'emergency_contact_name', 'emergency_contact_phone', 'emergency_contact_2_name',
    'emergency_contact_2_phone', 'medical_info',
]


def _clean(value):
    return value.strip() if isinstance(value, str) else value


def link(apps, schema_editor):
    MemberAccount = apps.get_model('network', 'MemberAccount')
    Member = apps.get_model('members', 'Member')
    Guardian = apps.get_model('members', 'Guardian')
    for account in MemberAccount.objects.select_related('user'):
        email = (account.user.email or account.user.username or '').strip().lower()
        members = list(Member.objects.filter(account=account).order_by('-is_active', 'pk'))
        holders, seen_clubs = [], set()
        for member in members:
            own = email and (member.email or '').strip().lower() == email and member.organisation_id not in seen_clubs
            member.account_role = 'holder' if own else 'dependant'
            member.save(update_fields=['account_role'])
            if own:
                seen_clubs.add(member.organisation_id)
                holders.append(member)
        if email:
            Guardian.objects.filter(member__account=account, account__isnull=True,
                                    email__iexact=email).update(account=account)
        filled = []
        for field in DETAILS:
            if getattr(account, field) not in ('', None):
                continue
            values = {_clean(getattr(m, field)) for m in holders if m.is_active} - {'', None}
            if len(values) == 1:
                setattr(account, field, values.pop())
                filled.append(field)
        if filled:
            account.save(update_fields=filled)


def unlink(apps, schema_editor):
    apps.get_model('members', 'Member').objects.update(account_role='')
    apps.get_model('members', 'Guardian').objects.update(account=None)


class Migration(migrations.Migration):

    dependencies = [
        ('members', '0019_account_links'),
        ('network', '0002_account_details'),
    ]

    operations = [migrations.RunPython(link, unlink)]
