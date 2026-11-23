from django.contrib.auth.models import User
from django.conf import settings
from django.db import models
from django.utils import timezone
from organisations.disciplines import DISCIPLINES, LABELS
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
    # Which martial art the grades are in (organisations/disciplines.py), so a member's progression
    # can be shown by discipline when it follows them from club to club.
    discipline = models.CharField(max_length=20, blank=True, choices=DISCIPLINES)

    @property
    def discipline_label(self):
        return LABELS.get(self.discipline, '')

    def __str__(self):
        return f"{self.organisation} — {self.name}"

    class Meta:
        ordering = ['organisation', 'order', 'name']
        unique_together = ('organisation', 'name')


class SyllabusSection(models.Model):
    """
    A block of syllabus content (techniques, theory, requirements) for one or
    more progression stages to share. Several stages — even across different
    systems — can point at the same section, e.g. a "Groundwork basics"
    section linked to both "Yellow Belt" (Kyu Grades) and "Stage 3" (Mon
    Grades) so the content is written once and reused.
    """
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='syllabus_sections')
    name = models.CharField(max_length=255)
    content = models.TextField(
        blank=True,
        help_text='Optional intro/description for this section — the actual requirements are the checklist items below it.',
    )
    order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.organisation} — {self.name}"

    def grouped_items(self):
        """Items grouped by subsection, in subsection order; ungrouped items form a final group with subsection=None."""
        by_subsection = {}
        for item in self.items.all():
            by_subsection.setdefault(item.subsection_id, []).append(item)
        groups = [
            {'subsection': sub, 'items': by_subsection.get(sub.pk, [])}
            for sub in self.subsections.all()
        ]
        ungrouped = by_subsection.get(None, [])
        if ungrouped:
            groups.append({'subsection': None, 'items': ungrouped})
        return groups

    class Meta:
        ordering = ['organisation', 'order', 'name']


def split_syllabus_columns(groups):
    """Split ordered checklist groups into two columns of near-equal height.

    A sub-section is never split across columns; items with no sub-section can
    be. Each entry in a group is a dict that may carry the item (``entry['item']``)
    so its description can be counted.
    """
    def entry_height(entry):
        item = entry['item'] if isinstance(entry, dict) else entry
        return 1 + (0.6 if getattr(item, 'description', '') else 0)

    # Units are the pieces we may put a column break between.
    units = []
    for g in groups:
        if g['subsection'] or not g['items']:
            height = (1 if g['subsection'] else 0) + sum(entry_height(e) for e in g['items'])
            units.append((g['subsection'], list(g['items']), height))
        else:
            units.extend((None, [e], entry_height(e)) for e in g['items'])

    total = sum(u[2] for u in units)
    best_at, best_diff, running = len(units), total, 0
    for i, unit in enumerate(units[:-1]):
        running += unit[2]
        diff = abs(total - 2 * running)
        if diff < best_diff:
            best_at, best_diff = i + 1, diff

    def regroup(part):
        out = []
        for subsection, entries, _ in part:
            if subsection is None and entries and out and out[-1]['subsection'] is None and out[-1]['items']:
                out[-1]['items'].extend(entries)
            else:
                out.append({'subsection': subsection, 'items': list(entries)})
        return out

    return [regroup(units[:best_at]), regroup(units[best_at:])]


class SyllabusSubsection(models.Model):
    """
    An optional header within a SyllabusSection used to split its checklist
    into parts, e.g. "Throws" and "Groundwork" within a "Yellow Belt"
    section. Purely organisational — items still belong to the section.
    """
    section = models.ForeignKey(SyllabusSection, on_delete=models.CASCADE, related_name='subsections')
    name = models.CharField(max_length=255)
    order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.section} — {self.name}"

    class Meta:
        ordering = ['section', 'order', 'name']


class SyllabusItem(models.Model):
    """
    One checkable requirement within a SyllabusSection, e.g. "O-goshi" or
    "Break-falls — both sides" under a "Groundwork basics" section. Staff
    tick these off per member (MemberSyllabusProgress) as they're covered.
    Optionally grouped under a SyllabusSubsection header within the section.
    """
    section = models.ForeignKey(SyllabusSection, on_delete=models.CASCADE, related_name='items')
    subsection = models.ForeignKey(
        SyllabusSubsection, null=True, blank=True, on_delete=models.SET_NULL, related_name='items',
        help_text='Optional sub-header to group this item under within the section.',
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    link = models.URLField(
        blank=True,
        help_text='Optional link for this item, e.g. a technique video or reference page.',
    )
    order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.section} — {self.name}"

    class Meta:
        ordering = ['section', 'order', 'name']


class MemberSyllabusProgress(models.Model):
    """Whether a given member has had a given syllabus item signed off. Staff-only to change; view-only for the member."""
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='syllabus_progress')
    item = models.ForeignKey(SyllabusItem, on_delete=models.CASCADE, related_name='member_progress')
    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    def __str__(self):
        return f"{self.member} — {self.item} ({'done' if self.completed else 'pending'})"

    class Meta:
        unique_together = ('member', 'item')


class ProgressionStage(models.Model):
    system = models.ForeignKey(ProgressionSystem, on_delete=models.CASCADE, related_name='stages')
    name = models.CharField(max_length=255)
    colour = models.CharField(max_length=7, blank=True, help_text='Hex colour, e.g. #FF0000')
    order = models.PositiveIntegerField(default=0)
    is_default = models.BooleanField(
        default=False,
        help_text='New members are assigned this stage automatically when the system is set to auto-assign.',
    )
    syllabus_section = models.ForeignKey(
        SyllabusSection, null=True, blank=True, on_delete=models.SET_NULL, related_name='stages',
        help_text='Syllabus content shown to members currently at this stage.',
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
    # 'grading_events', not DojoUK's 'gradings': this fork's own gradings app already uses Organisation.gradings.
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='grading_events')
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


class MemberTechnique(models.Model):
    """
    A technique signed off for a member: a coach has seen them do it. Kept
    against the technique rather than one syllabus, so it counts in every
    syllabus that includes it.
    """
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='techniques_signed_off')
    technique = models.ForeignKey(Technique, on_delete=models.CASCADE, related_name='signed_off')
    signed_off_at = models.DateTimeField(default=timezone.now)
    signed_off_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )

    def __str__(self):
        return f"{self.member} — {self.technique}"

    class Meta:
        unique_together = ('member', 'technique')
