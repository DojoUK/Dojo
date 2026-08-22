from django.db import models
from organisations.models import Organisation
from members.models import Member


class ProgressionSystem(models.Model):
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='progression_systems')
    name = models.CharField(max_length=255)
    order = models.PositiveIntegerField(default=0)
    assign_to_new_members = models.BooleanField(
        default=False,
        help_text='Automatically assign new members to the default stage in this system.',
    )

    def __str__(self):
        return f"{self.organisation} — {self.name}"

    class Meta:
        ordering = ['organisation', 'order', 'name']
        unique_together = ('organisation', 'name')


class ProgressionStage(models.Model):
    system = models.ForeignKey(ProgressionSystem, on_delete=models.CASCADE, related_name='stages')
    name = models.CharField(max_length=255)
    colour = models.CharField(max_length=7, blank=True, help_text='Hex colour, e.g. #FF0000')
    order = models.PositiveIntegerField(default=0)
    is_default = models.BooleanField(
        default=False,
        help_text='New members are assigned this stage automatically when the system is set to auto-assign.',
    )

    def __str__(self):
        return f"{self.system.name} — {self.name}"

    @property
    def organisation(self):
        return self.system.organisation

    class Meta:
        ordering = ['system', 'order', 'name']
        unique_together = ('system', 'name')


class MemberProgression(models.Model):
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='progressions')
    stage = models.ForeignKey(ProgressionStage, on_delete=models.CASCADE, related_name='achievements')
    achieved_date = models.DateField()
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.member} — {self.stage.name} ({self.achieved_date})"

    class Meta:
        ordering = ['-achieved_date']


class Assessment(models.Model):
    """
    An umbrella for one or more assessment sessions — e.g. "Summer Grading
    2026" containing Primary, Junior, and Senior sessions, or a dance
    school's "Exam Day". Members attend sessions and can be awarded a stage
    from any of the organisation's progression systems.

    Unlike classes, assessments are visible and manageable by all staff
    (coaches and admins alike) — they're treated as a shared, org-wide
    activity rather than a per-class responsibility.
    """
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='assessments')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    @property
    def sessions_count(self):
        return self.sessions.count()

    @property
    def members_count(self):
        """Distinct members enrolled across any session of this assessment."""
        return Member.objects.filter(assessment_attendance__session__assessment=self).distinct().count()

    def __str__(self):
        return f"{self.organisation} — {self.name}"

    class Meta:
        ordering = ['organisation', 'name']


class AssessmentSession(models.Model):
    """
    A dated session within an assessment. Members are enrolled directly into
    a session (not into the parent Assessment), since who's being assessed
    can differ session to session:

        Summer Grading 2026
        > Primary
            > Member A
            > Member B
        > Junior
    """
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name='sessions')
    name = models.CharField(
        max_length=255, blank=True,
        help_text='Optional — lets you tell apart multiple sessions run on the same date (e.g. "Primary", "Morning").',
    )
    date = models.DateField()
    notes = models.TextField(blank=True)
    is_cancelled = models.BooleanField(default=False)

    def display_name(self):
        return self.name or f'Session — {self.date:%d %b %Y}'

    def __str__(self):
        return f"{self.assessment} — {self.display_name()}"

    class Meta:
        ordering = ['-date', 'name']


class AssessmentAttendance(models.Model):
    """
    A member's enrolment in, and outcome for, one assessment session. The
    row's mere existence is what "enrols" a member into a session;
    `present`/`new_stage`/`progression` are then filled in from the
    assessment register. `new_stage`/`progression` are only set once a
    promotion has actually been committed for this row; the linked
    `MemberProgression` row is the real, org-wide grade history record
    (shown on the member's profile) — this is just a pointer back to it so
    the register can show/undo what it created.
    """
    session = models.ForeignKey(AssessmentSession, on_delete=models.CASCADE, related_name='attendance')
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='assessment_attendance')
    present = models.BooleanField(default=False)
    new_stage = models.ForeignKey(
        ProgressionStage, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    progression = models.ForeignKey(
        MemberProgression, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )

    def __str__(self):
        status = 'Present' if self.present else 'Absent'
        return f"{self.member} — {self.session} — {status}"

    class Meta:
        unique_together = ('session', 'member')
