import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('classes', '0009_class_bookings'),
        ('progression', '0008_grading_events_related_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='class', name='min_age',
            field=models.PositiveSmallIntegerField(blank=True, null=True, help_text='Youngest age (in whole years) that can book this class in the member portal. Leave blank for no limit.'),
        ),
        migrations.AddField(
            model_name='class', name='max_age',
            field=models.PositiveSmallIntegerField(blank=True, null=True, help_text='Oldest age (in whole years, inclusive) that can book this class in the member portal. Leave blank for no limit.'),
        ),
        migrations.AddField(
            model_name='class', name='min_stage',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='progression.progressionstage', help_text='Lowest grade that can book this class in the member portal.'),
        ),
        migrations.AddField(
            model_name='class', name='max_stage',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='progression.progressionstage', help_text='Highest grade that can book this class in the member portal.'),
        ),
    ]
