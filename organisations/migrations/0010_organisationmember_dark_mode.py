from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organisations', '0009_organisationmember_calendar_colour'),
    ]

    operations = [
        migrations.AddField(
            model_name='organisationmember',
            name='dark_mode',
            field=models.BooleanField(
                default=False,
                help_text="Use the dark theme for this staff member's own view of the app.",
            ),
        ),
    ]
