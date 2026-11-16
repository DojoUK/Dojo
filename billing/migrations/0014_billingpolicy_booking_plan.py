import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0013_merge_invoice_cancellation_and_dojouk'),
    ]

    operations = [
        migrations.AddField(
            model_name='billingpolicy', name='member_selectable',
            field=models.BooleanField(default=False, help_text='Members can pick this as their plan from the Book page of the member portal.'),
        ),
        migrations.AddField(
            model_name='billingpolicy', name='plan_description',
            field=models.TextField(blank=True, help_text='Shown to members when they choose a plan (the description above is internal).'),
        ),
        migrations.AddField(
            model_name='billingpolicy', name='session_limit',
            field=models.PositiveSmallIntegerField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(1)], help_text='The most sessions a member on this policy can be booked into in each period. Blank means unlimited.'),
        ),
        migrations.AddField(
            model_name='billingpolicy', name='limit_period',
            field=models.CharField(choices=[('week', 'a week'), ('month', 'a month')], default='month', max_length=10),
        ),
    ]
