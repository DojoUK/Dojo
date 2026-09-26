from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organisations', '0010_organisationmember_dark_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='organisationmember',
            name='accent_theme',
            field=models.CharField(
                blank=True,
                choices=[
                    ('', 'Club default'),
                    ('blue', 'Blue'),
                    ('green', 'Green'),
                    ('purple', 'Purple'),
                    ('amber', 'Amber'),
                    ('rose', 'Rose'),
                    ('slate', 'Slate'),
                ],
                default='',
                help_text="Secondary personal theme — recolours the sidebar and buttons for this staff member's own view. Works alongside dark mode.",
                max_length=20,
            ),
        ),
    ]
