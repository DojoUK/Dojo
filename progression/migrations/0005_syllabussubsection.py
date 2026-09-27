import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('progression', '0004_syllabusitem_link'),
    ]

    operations = [
        migrations.CreateModel(
            name='SyllabusSubsection',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('order', models.PositiveIntegerField(default=0)),
                ('section', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='subsections', to='progression.syllabussection')),
            ],
            options={
                'ordering': ['section', 'order', 'name'],
            },
        ),
        migrations.AddField(
            model_name='syllabusitem',
            name='subsection',
            field=models.ForeignKey(blank=True, help_text='Optional sub-header to group this item under within the section.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='items', to='progression.syllabussubsection'),
        ),
    ]
