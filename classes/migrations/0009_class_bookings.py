import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('members', '0001_initial'),
        ('classes', '0008_classhelper_sessionhelper'),
    ]

    operations = [
        migrations.AddField(
            model_name='class',
            name='is_active',
            field=models.BooleanField(default=False, help_text='Active classes appear in the member portal timetable, where members can enrol or book single sessions. The description above is shown to them.'),
        ),
        migrations.AddField(
            model_name='classmember',
            name='auto_enrol',
            field=models.BooleanField(default=True, help_text='On: the member is on every register for this class. Off: they are enrolled but only appear on the registers of sessions they have booked.'),
        ),
        migrations.CreateModel(
            name='SessionBooking',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='session_bookings', to='members.member')),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='bookings', to='classes.session')),
            ],
            options={
                'unique_together': {('session', 'member')},
            },
        ),
    ]
