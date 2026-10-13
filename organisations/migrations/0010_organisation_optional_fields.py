from django.db import migrations, models

# Every optional field that existed when field switches were introduced.
# Clubs that already exist keep all of them on, so nothing disappears on
# upgrade.
EXISTING_FIELDS = (
    'date_of_birth', 'phone', 'address', 'emergency_contact_2', 'medical_info',
    'joined_date', 'guardians', 'licence', 'staff_emergency_contact', 'dbs',
    'coaching_licence',
)


def switch_on_existing_fields(apps, schema_editor):
    Organisation = apps.get_model('organisations', 'Organisation')
    for org in Organisation.objects.all():
        org.optional_fields = {key: True for key in EXISTING_FIELDS}
        org.save(update_fields=['optional_fields'])


class Migration(migrations.Migration):

    dependencies = [
        ('organisations', '0009_organisation_features'),
    ]

    operations = [
        migrations.AddField(
            model_name='organisation',
            name='optional_fields',
            field=models.JSONField(blank=True, default=dict, help_text='Optional built-in fields switched on or off, {key: bool}. See organisations/optional_fields.py.'),
        ),
        migrations.RunPython(switch_on_existing_fields, migrations.RunPython.noop),
    ]
