import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('members', '0013_familygroup_familygroupmember_familygroup_members'),
        ('progression', '0002_syllabussection_progressionstage_syllabus_section'),
    ]

    operations = [
        migrations.AlterField(
            model_name='syllabussection',
            name='content',
            field=models.TextField(
                blank=True,
                help_text='Optional intro/description for this section — the actual requirements are the checklist items below it.',
            ),
        ),
        migrations.CreateModel(
            name='SyllabusItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('order', models.PositiveIntegerField(default=0)),
                ('section', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='progression.syllabussection')),
            ],
            options={
                'ordering': ['section', 'order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='MemberSyllabusProgress',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('completed', models.BooleanField(default=False)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('completed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='member_progress', to='progression.syllabusitem')),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='syllabus_progress', to='members.member')),
            ],
            options={
                'unique_together': {('member', 'item')},
            },
        ),
    ]
