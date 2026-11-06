from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('classes', '0006_session_leader'),
    ]

    operations = [
        migrations.AddField(
            model_name='class',
            name='default_leader',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='default_led_classes', to=settings.AUTH_USER_MODEL,
                help_text='The coach who normally runs this whole class series — carried onto newly generated '
                           'sessions and used on the calendar when a session has no leader of its own set yet.',
            ),
        ),
    ]
