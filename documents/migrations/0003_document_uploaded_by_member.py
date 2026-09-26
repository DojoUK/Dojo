from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0002_waivertemplate_signedwaiver'),
    ]

    operations = [
        migrations.AddField(
            model_name='document',
            name='uploaded_by_member',
            field=models.BooleanField(
                default=False,
                help_text='Uploaded by the member themselves via the member portal, rather than by staff.',
            ),
        ),
    ]
