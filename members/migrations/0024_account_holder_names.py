"""
First name and surname are now kept in the member's Dojo account, like their preferred name and
pronouns. Where an account holder's club record has no surname (e.g. it was saved as one word)
and their account has one, the record takes the account's first name and surname. Records that
already have a surname are left alone until the member next saves My details.
"""
from django.db import migrations


def fill(apps, schema_editor):
    Member = apps.get_model('members', 'Member')
    holders = (Member.objects.filter(account_role='holder', account__isnull=False, is_active=True, surname='')
               .exclude(account__surname='').select_related('account'))
    for member in holders:
        first, surname = member.account.first_name, member.account.surname
        name = ' '.join(p for p in (first, surname) if p)[:255]
        Member.objects.filter(pk=member.pk).update(first_name=first, surname=surname, name=name)


class Migration(migrations.Migration):

    dependencies = [
        ('members', '0023_split_names'),
    ]

    operations = [migrations.RunPython(fill, migrations.RunPython.noop)]
