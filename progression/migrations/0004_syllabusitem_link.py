from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('progression', '0003_syllabusitem_membersyllabusprogress'),
    ]

    operations = [
        migrations.AddField(
            model_name='syllabusitem',
            name='link',
            field=models.URLField(blank=True, help_text='Optional link for this item, e.g. a technique video or reference page.'),
        ),
    ]
