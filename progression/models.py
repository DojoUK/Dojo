from django.contrib.auth.models import User
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
