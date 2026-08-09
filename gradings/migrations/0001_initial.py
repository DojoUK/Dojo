# Generated manually, mirroring classes/migrations/0001_initial.py

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('members', '0013_familygroup_familygroupmember_familygroup_members'),
        ('organisations', '0007_staffholiday'),
        ('progression', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Grading',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('organisation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='gradings', to='organisations.organisation')),
            ],
            options={
                'ordering': ['organisation', 'name'],
            },
        ),
        migrations.CreateModel(
            name='GradingSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField()),
                ('notes', models.TextField(blank=True)),
                ('is_cancelled', models.BooleanField(default=False)),
                ('grading', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sessions', to='gradings.grading')),
            ],
            options={
                'ordering': ['-date'],
            },
        ),
        migrations.CreateModel(
            name='GradingMember',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('grading', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='entries', to='gradings.grading')),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='grading_entries', to='members.member')),
            ],
            options={
                'unique_together': {('grading', 'member')},
            },
        ),
        migrations.CreateModel(
            name='GradingAttendance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('present', models.BooleanField(default=False)),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='grading_attendance', to='members.member')),
                ('new_stage', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='progression.progressionstage')),
                ('progression', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='progression.memberprogression')),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attendance', to='gradings.gradingsession')),
            ],
            options={
                'unique_together': {('session', 'member')},
            },
        ),
    ]
