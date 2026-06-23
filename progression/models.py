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
