import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0015_billingpolicy_is_family_plan'),
    ]

    operations = [
        migrations.AddField(
            model_name='billingpolicy',
            name='max_family_members',
            field=models.PositiveSmallIntegerField(
                blank=True, null=True, validators=[django.core.validators.MinValueValidator(2)],
                help_text='Family plans only: the most family members the plan can cover. Blank for no limit.',
            ),
        ),
    ]
