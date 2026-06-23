from django.db import models
from django.contrib.auth.models import User
from organisations.models import Organisation
from members.models import Member


class Class(models.Model):
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='classes')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    schedule = models.JSONField(default=list, blank=True)
    # e.g. [{"day": 1, "time": "18:00"}, {"day": 3, "time": "18:00"}]
    # day follows Python weekday(): 0=Monday, 1=Tuesday, ..., 6=Sunday

    DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

    def schedule_display(self):
        if not self.schedule:
            return '—'
        parts = []
        for entry in self.schedule:
            time_str = entry.get('time', '')
            end_str = entry.get('end', '')
            if time_str and end_str:
                time_str = f"{time_str}–{end_str}"
            parts.append(f"{self.DAYS[entry['day']]} {time_str}".strip())
        return ', '.join(parts)

    max_capacity = models.PositiveIntegerField(null=True, blank=True)

    @property
    def enrolled_count(self):
        return self.enrolments.count()

    @property
    def is_full(self):
        return self.max_capacity is not None and self.enrolled_count >= self.max_capacity

    @property
    def spots_left(self):
        if self.max_capacity is None:
            return None
        return max(0, self.max_capacity - self.enrolled_count)

    def __str__(self):
        return f"{self.organisation} — {self.name}"

    class Meta:
        ordering = ['organisation', 'name']
        verbose_name = 'Class'
        verbose_name_plural = 'Classes'


class ClassCoach(models.Model):
    assigned_class = models.ForeignKey(Class, on_delete=models.CASCADE, related_name='coaches')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='coached_classes')

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} → {self.assigned_class}"

    class Meta:
        unique_together = ('assigned_class', 'user')


class ClassMember(models.Model):
    assigned_class = models.ForeignKey(Class, on_delete=models.CASCADE, related_name='enrolments')
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='enrolments')

    def __str__(self):
        return f"{self.member} → {self.assigned_class}"

    class Meta:
        unique_together = ('assigned_class', 'member')


class WaitingList(models.Model):
    assigned_class = models.ForeignKey(Class, on_delete=models.CASCADE, related_name='waiting_list')
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='waiting_list')
    joined_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.member} — waiting for {self.assigned_class}"

    class Meta:
        ordering = ['joined_at']
        unique_together = ('assigned_class', 'member')


class Session(models.Model):
    assigned_class = models.ForeignKey(Class, on_delete=models.CASCADE, related_name='sessions')
    date = models.DateField()
    notes = models.TextField(blank=True)
    is_cancelled = models.BooleanField(default=False)
    is_extra = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.assigned_class} — {self.date}"

    class Meta:
        ordering = ['-date']


class Attendance(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='attendance')
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='attendance')
    present = models.BooleanField(default=False)

    def __str__(self):
        status = 'Present' if self.present else 'Absent'
        return f"{self.member} — {self.session} — {status}"

    class Meta:
        unique_together = ('session', 'member')
