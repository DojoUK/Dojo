from django.db import models
from organisations.models import Organisation
from members.models import Member


class Grading(models.Model):
    """
    A grading — a roster of judoka being tracked towards promotion, mirroring
    how `classes.Class` groups members and generates dated `Session`s.

    Unlike classes, gradings are visible and manageable by all staff (coaches
    and admins alike), not just coaches assigned to it — grading is treated
    as a shared, org-wide activity rather than a per-class responsibility.
    """
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='gradings')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    @property
    def sessions_count(self):
        return self.sessions.count()

    @property
    def judoka_count(self):
        """Distinct members enrolled across any session of this grading."""
        return Member.objects.filter(grading_attendance__session__grading=self).distinct().count()

    def __str__(self):
        return f"{self.organisation} — {self.name}"

    class Meta:
        ordering = ['organisation', 'name']


class GradingSession(models.Model):
    """
    A dated grading event — mirrors classes.Session. Judoka are enrolled
    directly into a session (not into the parent Grading), since who's up
    for grading can differ session to session:

        Winter Grading 2026
        > Session 1
            > Judoka A
            > Judoka B
        > Session 2
    """
    grading = models.ForeignKey(Grading, on_delete=models.CASCADE, related_name='sessions')
    name = models.CharField(
        max_length=255, blank=True,
        help_text='Optional — lets you tell apart multiple sessions run on the same date (e.g. "Morning", "Kyu grades").',
    )
    date = models.DateField()
    notes = models.TextField(blank=True)
    is_cancelled = models.BooleanField(default=False)

    def display_name(self):
        return self.name or f'Session — {self.date:%d %b %Y}'

    def __str__(self):
        return f"{self.grading} — {self.display_name()}"

    class Meta:
        ordering = ['-date', 'name']


class GradingAttendance(models.Model):
    """
    A member's enrolment in, and outcome for, one grading session — mirrors
    classes.Attendance, plus the belt/stage awarded (if any). The row's mere
    existence is what "enrols" a judoka into a session (like classes.
    ClassMember does for a class); `present`/`new_stage`/`progression` are
    then filled in from the grading register. `new_stage`/`progression` are
    only set once a promotion has actually been committed for this row; the
    linked `progression.MemberProgression` row is the real, org-wide grade
    history record (shown on the member's profile), this is just a pointer
    back to it so the register can show/undo what it created.
    """
    session = models.ForeignKey(GradingSession, on_delete=models.CASCADE, related_name='attendance')
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='grading_attendance')
    present = models.BooleanField(default=False)
    new_stage = models.ForeignKey(
        'progression.ProgressionStage', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    progression = models.ForeignKey(
        'progression.MemberProgression', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )

    def __str__(self):
        status = 'Present' if self.present else 'Absent'
        return f"{self.member} — {self.session} — {status}"

    class Meta:
        unique_together = ('session', 'member')
