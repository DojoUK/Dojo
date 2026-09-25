import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organisations', '0001_initial'),
        ('progression', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='SyllabusSection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('content', models.TextField(blank=True, help_text='What members at this stage should know or practice — shown on their profile and member portal.')),
                ('order', models.PositiveIntegerField(default=0)),
                ('organisation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='syllabus_sections', to='organisations.organisation')),
            ],
            options={
                'ordering': ['organisation', 'order', 'name'],
            },
        ),
        migrations.AddField(
            model_name='progressionstage',
            name='syllabus_section',
            field=models.ForeignKey(
                blank=True, help_text='Syllabus content shown to members currently at this stage.',
                null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='stages',
                to='progression.syllabussection',
            ),
        ),
    ]
