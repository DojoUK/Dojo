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

    is_active = models.BooleanField(
        default=False,
        help_text='Active classes appear in the member portal timetable, where members can enrol or '
                  'book single sessions. The description above is shown to them.',
    )
    min_age = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text='Youngest age (in whole years) that can book this class in the member portal. Leave blank for no limit.',
    )
    max_age = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text='Oldest age (in whole years, inclusive) that can book this class in the member portal. Leave blank for no limit.',
    )
    min_stage = models.ForeignKey(
        'progression.ProgressionStage', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
        help_text='Lowest grade that can book this class in the member portal.',
    )
    max_stage = models.ForeignKey(
        'progression.ProgressionStage', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
        help_text='Highest grade that can book this class in the member portal.',
    )
    max_capacity = models.PositiveIntegerField(null=True, blank=True)
    billing_policy = models.ForeignKey('billing.BillingPolicy', null=True, blank=True, on_delete=models.SET_NULL, related_name='classes')
    default_leader = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='default_led_classes',
        help_text='The coach who normally runs this whole class series — carried onto newly generated '
                   'sessions and used on the calendar when a session has no leader of its own set yet.',
    )

    @property
    def has_restrictions(self):
        return any([self.min_age is not None, self.max_age is not None, self.min_stage_id, self.max_stage_id])

    def restriction_text(self):
        """Plain-English summary of who can book, e.g. 'Ages 7 to 11 · Orange belt and above'."""
        parts = []
        if self.min_age is not None and self.max_age is not None:
            parts.append(f'Ages {self.min_age} to {self.max_age}')
        elif self.min_age is not None:
            parts.append(f'Ages {self.min_age} and over')
        elif self.max_age is not None:
            parts.append(f'Ages {self.max_age} and under')
        if self.min_stage_id and self.max_stage_id:
            parts.append(self.min_stage.name if self.min_stage_id == self.max_stage_id
                         else f'{self.min_stage.name} to {self.max_stage.name}')
        elif self.min_stage_id:
            parts.append(f'{self.min_stage.name} and above')
        elif self.max_stage_id:
            parts.append(f'{self.max_stage.name} and below')
        return ' · '.join(parts)

    def ineligible_reason(self, member, today=None):
        """Why this member can't book the class from the portal (age / grade limits), or '' if they can."""
        from django.utils import timezone
        if self.min_age is not None or self.max_age is not None:
            if not member.date_of_birth:
                return "We don't have your date of birth, so we can't check the age range. Please ask the club."
            today = today or timezone.localdate()
            dob = member.date_of_birth
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            if (self.min_age is not None and age < self.min_age) or (self.max_age is not None and age > self.max_age):
                return f'This class is for {self.restriction_text().split(" · ")[0].lower()}.'
        stages = [st for st in (self.min_stage, self.max_stage) if st is not None]
        if stages and self.organisation.has_feature('grades'):
            system_id = stages[0].system_id
            current = None
            for prog in member.progressions.filter(stage__system_id=system_id).select_related('stage'):
                if current is None or (prog.stage.order, prog.achieved_date) > (current.stage.order, current.achieved_date):
                    current = prog
            level = current.stage.order if current else None
            low = self.min_stage.order if self.min_stage_id else None
            high = self.max_stage.order if self.max_stage_id else None
            if (low is not None and (level is None or level < low)) or (high is not None and level is not None and level > high):
                grade = self.restriction_text().split(' · ')[-1]
                return f'This class is for grades: {grade}.'
        return ''

    @property
    def enrolled_count(self):
        return self.enrolments.count()

    @property
    def has_capacity_limit(self):
        """Capacity only applies while the club has waiting lists switched on."""
        return self.max_capacity is not None and self.organisation.has_feature('waiting_lists')

    @property
    def is_full(self):
        return self.has_capacity_limit and self.enrolled_count >= self.max_capacity

    @property
    def spots_left(self):
        if not self.has_capacity_limit:
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


class ClassHelper(models.Model):
    """
    An organisation member (not a staff account) who regularly helps out at
    this class's sessions — e.g. a senior student assisting a junior class.
    Mirrors ClassCoach, but points at members.Member rather than a User,
    since helpers aren't staff and don't log in to run sessions themselves.
    """
    assigned_class = models.ForeignKey(Class, on_delete=models.CASCADE, related_name='helpers')
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='helping_classes')

    def __str__(self):
        return f"{self.member} → {self.assigned_class} (helper)"

    class Meta:
        unique_together = ('assigned_class', 'member')
        ordering = ['member__name']


class ClassMember(models.Model):
    assigned_class = models.ForeignKey(Class, on_delete=models.CASCADE, related_name='enrolments')
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='enrolments')
    auto_enrol = models.BooleanField(
        default=True,
        help_text='On: the member is on every register for this class. Off: they are enrolled but only '
                  'appear on the registers of sessions they have booked.',
    )

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
    leader = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='led_sessions',
        help_text='The staff member actually running this session — set from the register.',
    )

    def __str__(self):
        return f"{self.assigned_class} — {self.date}"

    class Meta:
        ordering = ['-date']


class SessionBooking(models.Model):
    """A member who has booked onto one session without being enrolled on every register for the class."""
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='bookings')
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='session_bookings')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.member} → {self.session}"

    class Meta:
        unique_together = ('session', 'member')


class SessionAbsence(models.Model):
    """A member enrolled on every register for a class who has told the club they can't make one session."""
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='absences')
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='session_absences')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.member} can't attend {self.session}"

    class Meta:
        unique_together = ('session', 'member')


class Attendance(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='attendance')
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='attendance')
    present = models.BooleanField(default=False)

    def __str__(self):
        status = 'Present' if self.present else 'Absent'
        return f"{self.member} — {self.session} — {status}"

    class Meta:
        unique_together = ('session', 'member')


class SessionCoach(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='session_coaches')
    coach = models.ForeignKey(User, on_delete=models.CASCADE, related_name='coached_sessions')
    present = models.BooleanField(default=False)

    def __str__(self):
        status = 'Present' if self.present else 'Absent'
        return f"{self.coach.get_full_name() or self.coach.username} — {self.session} — {status}"

    class Meta:
        unique_together = ('session', 'coach')
        ordering = ['coach__first_name', 'coach__last_name']


class SessionHelper(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='session_helpers')
    helper = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='helped_sessions')
    present = models.BooleanField(default=False)

    def __str__(self):
        status = 'Present' if self.present else 'Absent'
        return f"{self.helper} — {self.session} — {status}"

    class Meta:
        unique_together = ('session', 'helper')
        ordering = ['helper__name']
