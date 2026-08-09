from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gradings', '0002_remove_gradingmember'),
    ]

    operations = [
        migrations.AddField(
            model_name='gradingsession',
            name='name',
            field=models.CharField(
                blank=True, max_length=255,
                help_text='Optional — lets you tell apart multiple sessions run on the same date (e.g. "Morning", "Kyu grades").',
            ),
        ),
        migrations.AlterModelOptions(
            name='gradingsession',
            options={'ordering': ['-date', 'name']},
        ),
    ]
