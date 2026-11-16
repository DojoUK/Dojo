import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('members', '0001_initial'),
        ('classes', '0010_class_restrictions'),
    ]

    operations = [
        migrations.CreateModel(
            name='SessionAbsence',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='session_absences', to='members.member')),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='absences', to='classes.session')),
            ],
            options={'unique_together': {('session', 'member')}},
        ),
    ]
