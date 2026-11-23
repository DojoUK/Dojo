"""
Split everyone's existing full name into first name and surname: the last word is the surname and
everything before it the first name ("Mary Jane Smith" → "Mary Jane" + "Smith"). A single word is
just a first name. Staff can correct any that came out wrong (e.g. a two-word surname). The full
name itself is left as it is.
"""
from django.db import migrations


def split_name(full):
    parts = (full or '').split()
    if not parts:
        return '', ''
    if len(parts) == 1:
        return parts[0], ''
    return ' '.join(parts[:-1]), parts[-1]


def split(apps, schema_editor):
    for app_label, model_name in (('members', 'Member'), ('members', 'MemberApplication'), ('network', 'MemberAccount')):
        model = apps.get_model(app_label, model_name)
        for person in model.objects.filter(first_name='', surname='').exclude(name='').only('pk', 'name'):
            first, surname = split_name(person.name)
            model.objects.filter(pk=person.pk).update(first_name=first[:150], surname=surname[:150])


class Migration(migrations.Migration):

    dependencies = [
        ('members', '0022_name_parts'),
        ('network', '0004_name_parts'),
    ]

    operations = [migrations.RunPython(split, migrations.RunPython.noop)]
