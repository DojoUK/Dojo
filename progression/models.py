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
    uses_gradings = models.BooleanField(
        default=False,
        help_text='Promotions in this system happen at grading events, rather than being awarded '
                  'by a coach whenever someone is ready.',
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
    syllabus = models.ForeignKey(
        'Syllabus', null=True, blank=True, on_delete=models.SET_NULL, related_name='stages',
        help_text='What members at this grade are working on. Several grades can share one syllabus.',
    )

    def __str__(self):
        return f"{self.system.name} — {self.name}"

    @property
    def organisation(self):
        return self.system.organisation

    def next_stage(self):
        return self.system.stages.filter(order__gt=self.order).order_by('order', 'name').first()

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


class Grading(models.Model):
    """
    A grading in one progression system, run as one or more sessions. A
    club that grades everyone together has a single session and never sees
    the session level; a club that splits a grading weekend into groups
    (often matching its weekly classes) adds a session per group.
    """
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='gradings')
    name = models.CharField(max_length=255)
    system = models.ForeignKey(ProgressionSystem, on_delete=models.PROTECT, related_name='gradings')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.organisation} — {self.name}"

    @property
    def first_date(self):
        first = self.sessions.order_by('date').first()
        return first.date if first else None

    class Meta:
        ordering = ('-created_at',)


class GradingSession(models.Model):
    """One group within a grading, graded on a given date."""
    grading = models.ForeignKey(Grading, on_delete=models.CASCADE, related_name='sessions')
    name = models.CharField(
        max_length=255, blank=True,
        help_text='Optional, e.g. "Juniors" or "Saturday morning". Useful once a grading has several sessions.',
    )
    date = models.DateField()

    def __str__(self):
        return f"{self.grading.name} — {self.display_name}"

    @property
    def display_name(self):
        return self.name or f"{self.date:%d %b %Y}"

    class Meta:
        ordering = ('date', 'name', 'pk')


class GradingCandidate(models.Model):
    """
    A member being graded in a session. A pass creates one MemberProgression
    (the member's grade history entry), linked here so changing the result
    later updates or removes that same record.
    """
    class Result(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PASSED = 'passed', 'Passed'
        FAILED = 'failed', 'Failed'
        ABSENT = 'absent', 'Absent'

    session = models.ForeignKey(GradingSession, on_delete=models.CASCADE, related_name='candidates')
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='grading_candidacies')
    target_stage = models.ForeignKey(
        ProgressionStage, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
        help_text='The grade they are going for.',
    )
    result = models.CharField(max_length=10, choices=Result.choices, default=Result.PENDING)
    notes = models.TextField(blank=True, help_text="Examiner's notes.")
    progression = models.OneToOneField(
        MemberProgression, null=True, blank=True, on_delete=models.SET_NULL, related_name='grading_candidate',
    )

    def __str__(self):
        return f"{self.member} — {self.session} ({self.get_result_display()})"

    class Meta:
        ordering = ('member__name',)
        unique_together = ('session', 'member')


class Technique(models.Model):
    """
    One entry in the club's technique library, e.g. "Ushiro ukemi". Written
    once and used in as many syllabuses as include it, so teaching it counts
    towards every one of them.
    """
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='techniques')
    name = models.CharField(max_length=255)
    category = models.CharField(
        max_length=100, blank=True, help_text='Optional grouping, e.g. "Nage Waza" or "Ukemi".',
    )
    description = models.TextField(blank=True)
    link = models.URLField(blank=True, help_text='Optional https:// link, e.g. a video of the technique.')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ('category', 'name', 'pk')


class Syllabus(models.Model):
    """What members are working on between gradings, e.g. "White to Yellow"."""
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='syllabuses')
    name = models.CharField(max_length=255)
    introduction = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.name

    def grouped_entries(self):
        """[(heading or None, [entries])] in display order; entries without a heading come last."""
        entries = list(self.entries.select_related('technique', 'heading'))
        groups = [(heading, [e for e in entries if e.heading_id == heading.pk]) for heading in self.headings.all()]
        loose = [e for e in entries if e.heading_id is None]
        if loose:
            groups.append((None, loose))
        return groups

    class Meta:
        ordering = ('order', 'name', 'pk')
        verbose_name_plural = 'syllabuses'


class SyllabusHeading(models.Model):
    """A sub-heading within a syllabus, e.g. "Nage Waza - Throwing Techniques"."""
    syllabus = models.ForeignKey(Syllabus, on_delete=models.CASCADE, related_name='headings')
    name = models.CharField(max_length=255)
    order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ('order', 'pk')


class SyllabusEntry(models.Model):
    """A technique in a syllabus, optionally under a heading."""
    syllabus = models.ForeignKey(Syllabus, on_delete=models.CASCADE, related_name='entries')
    heading = models.ForeignKey(
        SyllabusHeading, null=True, blank=True, on_delete=models.SET_NULL, related_name='entries',
    )
    technique = models.ForeignKey(Technique, on_delete=models.PROTECT, related_name='syllabus_entries')
    notes = models.TextField(blank=True, help_text='Anything specific to this syllabus, e.g. "both sides".')
    order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.syllabus} — {self.technique}"

    class Meta:
        ordering = ('order', 'pk')
        unique_together = ('syllabus', 'technique')
