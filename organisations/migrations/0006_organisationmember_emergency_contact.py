# Generated manually to add coach emergency contact details

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organisations', '0005_organisation_custom_css_organisation_logo'),
    ]

    operations = [
        migrations.AddField(
            model_name='organisationmember',
            name='emergency_contact_name',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='organisationmember',
            name='emergency_contact_phone',
            field=models.CharField(blank=True, max_length=30),
        ),
    ]
