from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('classes', '0005_sessioncoach'),
    ]

    operations = [
        migrations.AddField(
            model_name='session',
            name='leader',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='led_sessions', to=settings.AUTH_USER_MODEL,
                help_text='The staff member actually running this session — set from the register.',
            ),
        ),
    ]
