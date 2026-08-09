# Enrolment moves from grading-level (GradingMember) to per-session
# (GradingAttendance row existence) so judoka can be enrolled per grading
# session rather than once for the whole grading.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('gradings', '0001_initial'),
    ]

    operations = [
        migrations.DeleteModel(
            name='GradingMember',
        ),
    ]
