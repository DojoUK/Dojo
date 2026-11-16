from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify
from django.utils import timezone
from django.utils.functional import cached_property


class Organisation(models.Model):
    # Choices for logging staff out after inactivity, in minutes. 0 means
    # never: the session then lasts the default two weeks.
    SESSION_TIMEOUT_CHOICES = (
        (15, '15 minutes'), (30, '30 minutes'), (60, '1 hour'), (120, '2 hours'),
        (240, '4 hours'), (480, '8 hours'), (0, "Don't log out automatically"),
    )
    DEFAULT_SESSION_TIMEOUT = 120

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    website = models.URLField(blank=True)
    settings = models.JSONField(default=dict, blank=True)
    features = models.JSONField(
        default=dict, blank=True,
        help_text='Optional features switched on or off, {key: bool}. See organisations/features.py.',
    )
    optional_fields = models.JSONField(
        default=dict, blank=True,
        help_text='Optional built-in fields switched on or off, {key: bool}. See organisations/optional_fields.py.',
    )
    logo = models.ImageField(upload_to='logos/', null=True, blank=True)
    custom_css = models.TextField(blank=True)
    subscription_tier = models.CharField(max_length=50, default='free')
    created_at = models.DateTimeField(auto_now_add=True)

    def theme(self):
        s = self.settings or {}
        return {
            'sidebar_color': s.get('sidebar_color', '#1E3A5F'),
            'sidebar_color_dark': s.get('sidebar_color_dark', '#152d4a'),
            'accent_color': s.get('accent_color', '#2563EB'),
            'accent_hover': s.get('accent_hover', '#1d4ed8'),
        }

    @cached_property
    def enabled_features(self):
        """Keys of the optional features switched on. In templates:
        {% if 'billing' in org.enabled_features %}"""
        from .features import effective_features
        return effective_features(self.features)

    def has_feature(self, key):
        from .features import FEATURES_BY_KEY
        if key not in FEATURES_BY_KEY:
            raise ValueError(f'Unknown feature: {key}')
        return key in self.enabled_features

    def set_features(self, choices):
        """Store {key: bool} choices and drop the cached effective set."""
        self.features = dict(choices)
        self.__dict__.pop('enabled_features', None)

    @cached_property
    def enabled_fields(self):
        """Keys of the optional built-in fields switched on. In templates:
        {% if 'phone' in org.enabled_fields %}"""
        from .optional_fields import effective_fields
        return effective_fields(self.optional_fields)

    def has_field(self, key):
        from .optional_fields import OPTIONAL_FIELDS_BY_KEY
        if key not in OPTIONAL_FIELDS_BY_KEY:
            raise ValueError(f'Unknown optional field: {key}')
        return key in self.enabled_fields

    def set_optional_fields(self, choices):
        self.optional_fields = dict(choices)
        self.__dict__.pop('enabled_fields', None)

    def field_label(self, key):
        """The club's own name for a renamable field, else its default label."""
        from .optional_fields import OPTIONAL_FIELDS_BY_KEY
        return (self.settings or {}).get(f'{key}_label') or OPTIONAL_FIELDS_BY_KEY[key].label

    @property
    def licence_label(self):
        return self.field_label('licence')

    @property
    def coaching_licence_label(self):
        return self.field_label('coaching_licence')

    @property
    def sidebar_name(self):
        """The short name from Settings → General, or the full name."""
        return (self.settings or {}).get('short_name') or self.name

    @property
    def logo_luminance(self):
        """How light (1) or dark (0) the logo is; measured on upload, or now for older logos."""
        if not self.logo:
            return None
        stored = (self.settings or {}).get('logo_luminance')
        if stored is not None:
            return stored
        from .branding import image_luminance
        try:
            with self.logo.open('rb') as file:
                value = round(image_luminance(file), 3)
        except (OSError, ValueError):
            return None
        self.settings = {**(self.settings or {}), 'logo_luminance': value}
        Organisation.objects.filter(pk=self.pk).update(settings=self.settings)
        return value

    # Settings → Security: who must use two-step sign-in (an authenticator app).
    TWO_FACTOR_ALL = 'all'
    TWO_FACTOR_ADMINS = 'admins'
    TWO_FACTOR_OPTIONAL = 'optional'
    TWO_FACTOR_CHOICES = (
        (TWO_FACTOR_ALL, 'Required for all staff'),
        (TWO_FACTOR_ADMINS, 'Required for admins only'),
        (TWO_FACTOR_OPTIONAL, 'Optional: each person chooses'),
    )

    @property
    def two_factor_policy(self):
        # Clubs made before two-step sign-in existed stay optional until an admin chooses;
        # new clubs are set to required at setup.
        value = (self.settings or {}).get('two_factor', self.TWO_FACTOR_OPTIONAL)
        return value if value in dict(self.TWO_FACTOR_CHOICES) else self.TWO_FACTOR_OPTIONAL

    def requires_two_factor(self, membership):
        policy = self.two_factor_policy
        return policy == self.TWO_FACTOR_ALL or (policy == self.TWO_FACTOR_ADMINS and membership.is_org_admin)

    @property
    def session_timeout_minutes(self):
        value = (self.settings or {}).get('session_timeout_minutes', self.DEFAULT_SESSION_TIMEOUT)
        allowed = {minutes for minutes, _ in self.SESSION_TIMEOUT_CHOICES}
        return value if value in allowed else self.DEFAULT_SESSION_TIMEOUT

    @property
    def has_bookable_classes(self):
        """Whether any class is switched on for booking in the member portal."""
        return self.classes.filter(is_active=True).exists()

    DEFAULT_BOOKING_CUTOFF_HOURS = 2
    MAX_BOOKING_CUTOFF_HOURS = 168

    @property
    def booking_cutoff_hours(self):
        """How many hours before a session starts members can no longer book or cancel it themselves."""
        value = (self.settings or {}).get('booking_cutoff_hours', self.DEFAULT_BOOKING_CUTOFF_HOURS)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= self.MAX_BOOKING_CUTOFF_HOURS:
            return self.DEFAULT_BOOKING_CUTOFF_HOURS
        return value

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class OrganisationMember(models.Model):
    class Role(models.TextChoices):
        ORG_ADMIN = 'org_admin', 'Org Admin'
        COACH = 'coach', 'Coach'

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='organisation_memberships')
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='members')
    role = models.CharField(max_length=20, choices=Role.choices)
    dbs_number = models.CharField(max_length=100, blank=True)
    dbs_expiry = models.DateField(null=True, blank=True)
    coaching_licence = models.CharField(max_length=100, blank=True)
    coaching_licence_expiry = models.DateField(null=True, blank=True)
    emergency_contact_name = models.CharField(max_length=255, blank=True)
    emergency_contact_phone = models.CharField(max_length=30, blank=True)
    emergency_contact_2_name = models.CharField(max_length=255, blank=True)
    emergency_contact_2_phone = models.CharField(max_length=30, blank=True)
    calendar_colour = models.CharField(
        max_length=7, blank=True,
        help_text="Hex colour used for this staff member's sessions on the org calendar, e.g. #2563EB.",
    )
    dark_mode = models.BooleanField(
        default=False,
        help_text="Use the dark theme for this staff member's own view of the app.",
    )

    class AccentTheme(models.TextChoices):
        DEFAULT = '', "Club default"
        BLUE = 'blue', 'Blue'
        GREEN = 'green', 'Green'
        PURPLE = 'purple', 'Purple'
        AMBER = 'amber', 'Amber'
        ROSE = 'rose', 'Rose'
        SLATE = 'slate', 'Slate'

    accent_theme = models.CharField(
        max_length=20, choices=AccentTheme.choices, blank=True, default='',
        help_text="Secondary personal theme — recolours the sidebar and buttons for this staff member's own view. Works alongside dark mode.",
    )

    # Extra permissions an org admin can grant to an individual coach. Org
    # admins implicitly hold all of them. Each entry is (name, label, help);
    # the matching model field is `can_<name>`.
    CAPABILITIES = (
        ('manage_billing', 'Manage billing',
         'Invoices, payments, billing policies, discounts and the finance report for the whole club.'),
        ('take_payments', 'Take payments',
         'View invoices and record payments for members of their own classes.'),
        ('view_all_attendance', 'View all attendance',
         'Attendance analytics and export for every class, not just their own.'),
        ('message_all_members', 'Message all members',
         'Send announcements to the whole club or any class, not just their own classes.'),
        ('manage_gradings', 'Manage gradings',
         'Run gradings: add candidates and record results, which updates members\' grades.'),
        ('generate_sessions', 'Generate sessions',
         'Generate upcoming sessions from the schedule of the classes they coach.'),
        ('manage_class_members', 'Enrol members',
         'Enrol members in the classes they coach and add other staff as coaches, from the class '
         'page or straight from the register.'),
        ('view_stock', 'View stock',
         'See the stock list and how many of each item are left, without changing it.'),
    )
    # This fork has always let every coach use these, so a coach added on the
    # Staff page starts with them ticked; an admin can untick any of them there.
    NEW_COACH_CAPABILITIES = (
        'manage_billing', 'view_all_attendance', 'manage_gradings',
        'generate_sessions', 'manage_class_members', 'view_stock',
    )
    can_manage_billing = models.BooleanField(default=False)
    can_take_payments = models.BooleanField(default=False)
    can_view_all_attendance = models.BooleanField(default=False)
    can_message_all_members = models.BooleanField(default=False)
    can_manage_gradings = models.BooleanField(default=False)
    can_generate_sessions = models.BooleanField(default=False)
    can_manage_class_members = models.BooleanField(default=False)
    can_view_stock = models.BooleanField(default=False)

    @property
    def is_org_admin(self):
        return self.role == self.Role.ORG_ADMIN

    def has_capability(self, capability):
        if capability not in {name for name, _, _ in self.CAPABILITIES}:
            raise ValueError(f'Unknown capability: {capability}')
        return self.is_org_admin or getattr(self, f'can_{capability}')

    def capability_states(self):
        """(name, label, help, granted) for every capability, for the staff permissions form."""
        return [(name, label, help, getattr(self, f'can_{name}')) for name, label, help in self.CAPABILITIES]

    def granted_capabilities(self):
        """(name, label) for each capability this coach has been explicitly granted."""
        return [(name, label) for name, label, _ in self.CAPABILITIES if getattr(self, f'can_{name}')]

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} — {self.organisation} ({self.get_role_display()})"

    class Meta:
        unique_together = ('user', 'organisation')


class StaffHoliday(models.Model):
    """
    A period during which a staff member (coach or admin) is away.

    Used to show holidays on the org calendar and to exclude the coach from
    the auto-populated coach list on the class register for sessions that
    fall within the holiday range.
    """
    member = models.ForeignKey(OrganisationMember, on_delete=models.CASCADE, related_name='holidays')
    start_date = models.DateField()
    end_date = models.DateField()
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        name = self.member.user.get_full_name() or self.member.user.username
        return f"{name}: {self.start_date} – {self.end_date}"

    class Meta:
        ordering = ['-start_date']


class Announcement(models.Model):
    class Recipients(models.TextChoices):
        ALL = 'all', 'All active members'
        CLASS = 'class', 'Specific class'
        CUSTOM = 'custom', 'Custom selection'

    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='announcements')
    subject = models.CharField(max_length=255)
    body = models.TextField()
    sent_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    sent_at = models.DateTimeField(default=timezone.now)
    recipient_count = models.PositiveIntegerField(default=0)
    recipient_label = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.subject} ({self.sent_at:%d %b %Y})"

    class Meta:
        ordering = ['-sent_at']
