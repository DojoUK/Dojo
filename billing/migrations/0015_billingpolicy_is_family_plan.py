from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0014_billingpolicy_booking_plan'),
    ]

    operations = [
        migrations.AddField(
            model_name='billingpolicy',
            name='is_family_plan',
            field=models.BooleanField(
                default=False,
                help_text='One plan for a whole family (members linked in a family group). A flat price is charged '
                          'once per family, per-session prices add up every child, and any session limit is shared.',
            ),
        ),
    ]
