# Generated manually to add staff holiday periods

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organisations', '0006_organisationmember_emergency_contact'),
    ]

    operations = [
        migrations.CreateModel(
            name='StaffHoliday',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('start_date', models.DateField()),
                ('end_date', models.DateField()),
                ('note', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='holidays', to='organisations.organisationmember')),
            ],
            options={
                'ordering': ['-start_date'],
            },
        ),
    ]
