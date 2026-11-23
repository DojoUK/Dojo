import secrets
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from organisations.models import Organisation

from .names import NameParts


def generate_token():
    return secrets.token_urlsafe(32)


class Member(NameParts, models.Model):
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='club_members')
    # The member's own Dojo account (network.MemberAccount), which can span several clubs. Several
    # members can share one, e.g. a parent's account for their children.
    account = models.ForeignKey('network.MemberAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='memberships')

    class AccountRole(models.TextChoices):
        NONE = '', 'Not linked'
        HOLDER = 'holder', 'Account holder'     # this record is the account holder themselves
        DEPENDANT = 'dependant', 'Dependant'    # someone the account holder looks after, e.g. their child

    # For an account holder's own record, the account owns the shared details (contact, address,
    # emergency contacts, medical information) and keeps this copy up to date (network/details.py).
    account_role = models.CharField(max_length=10, choices=AccountRole.choices, default='', blank=True)
    name = models.CharField(max_length=255)   # the full name: first name and surname (members/names.py)
    first_name = models.CharField(max_length=150, blank=True)
    surname = models.CharField(max_length=150, blank=True)
    preferred_name = models.CharField(max_length=150, blank=True, help_text='What they like to be called, if not their first name')
    pronouns = models.CharField(max_length=30, blank=True, help_text='e.g. she/her, he/him, they/them')
    date_of_birth = models.DateField(null=True, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    emergency_contact_name = models.CharField(max_length=255, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)
    emergency_contact_2_name = models.CharField(max_length=255, blank=True)
    emergency_contact_2_phone = models.CharField(max_length=20, blank=True)
    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField('town or city', max_length=100, blank=True)
    county = models.CharField(max_length=100, blank=True)
    postcode = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    # Reminder emails before sessions (classes/reminders.py); the member can switch them off in their portal.
    session_reminders = models.BooleanField('email reminders before sessions', default=True)
    token = models.CharField(max_length=64, unique=True, default=generate_token)
    token_created_at = models.DateTimeField(default=timezone.now, help_text='When the current portal token was issued — used to auto-rotate stale links')
    joined_date = models.DateField(null=True, blank=True)
    custom_field_values = models.JSONField(default=dict, blank=True)
    monthly_fee = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True)
    subscription_status = models.CharField(max_length=20, blank=True)
    licence_number = models.CharField(max_length=100, blank=True)
    licence_expiry = models.DateField(null=True, blank=True)
    medical_info = models.TextField(blank=True, help_text='Medical conditions, allergies, or other health information coaches should know about')
    billing_policy = models.ForeignKey('billing.BillingPolicy', null=True, blank=True, on_delete=models.SET_NULL, related_name='members')
    archived_at = models.DateTimeField(null=True, blank=True)
    retention_notes = models.TextField(blank=True, help_text='Reason for retaining data beyond the standard period')
    anonymised_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_account_holder(self):
        return bool(self.account_id) and self.account_role == self.AccountRole.HOLDER

    @property
    def has_active_subscription(self):
        return self.subscription_status == 'active'

    def anonymise(self):
        """Scrub personal data in place, keeping the row for financial/attendance FK integrity."""
        self.first_name, self.surname = 'Deleted', f'Member #{self.pk}'
        self.preferred_name = self.pronouns = ''
        self.date_of_birth = None
        self.email = ''
        self.phone = ''
        self.emergency_contact_name = ''
        self.emergency_contact_phone = ''
        self.emergency_contact_2_name = ''
        self.emergency_contact_2_phone = ''
        self.address_line1 = ''
        self.address_line2 = ''
        self.city = ''
        self.county = ''
        self.postcode = ''
        self.custom_field_values = {}
        self.stripe_customer_id = ''
        self.stripe_subscription_id = ''
        self.subscription_status = ''
        self.licence_number = ''
        self.licence_expiry = None
        self.medical_info = ''
        self.token = generate_token()
        self.token_created_at = timezone.now()
        self.is_active = False
        if not self.archived_at:
            self.archived_at = timezone.now()
        self.anonymised_at = timezone.now()
        # The club's copy is gone; the member's Dojo account (and their other clubs) are untouched.
        self.account = None
        self.account_role = ''
        self.save()
        for guardian in self.guardians.all():
            guardian.delete()
        for note in self.notes.all():
            note.delete()

    def session_counts(self):
        """Total sessions attended, and sessions helped on (None if the member has never been a helper)."""
        attended = self.attendance.filter(present=True).count()
        helped = self.helped_sessions.filter(present=True).count()
        is_helper = helped > 0 or self.helping_classes.exists() or self.helped_sessions.exists()
        return {'attended': attended, 'helped': helped if is_helper else None}

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class Guardian(models.Model):
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='guardians')
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    relationship = models.CharField(max_length=100, blank=True)
    # A parent with a Dojo account: their name, email and phone here follow the account.
    account = models.ForeignKey('network.MemberAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='guardian_of')

    def __str__(self):
        return f"{self.name} (guardian of {self.member})"


class CustomField(models.Model):
    class FieldType(models.TextChoices):
        TEXT = 'text', 'Text'
        DATE = 'date', 'Date'
        SELECT = 'select', 'Select'
        BOOLEAN = 'boolean', 'Boolean'

    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='custom_fields')
    name = models.CharField(max_length=255)
    field_type = models.CharField(max_length=20, choices=FieldType.choices)
    options = models.JSONField(default=list, blank=True)
    order = models.PositiveIntegerField(default=0)
    show_on_registers = models.BooleanField(
        default=False,
        help_text='Yes/no fields only: members ticked "yes" get a badge with the field name on class '
                  'pages and registers, e.g. "Photo consent".',
    )
    # Asked on the club's join form too, so the answer comes in with the application. Off by
    # default: some fields are only for staff, e.g. "Kit issued".
    on_application = models.BooleanField('ask on the join form', default=False)

    def __str__(self):
        return f"{self.organisation} — {self.name}"

    class Meta:
        ordering = ['organisation', 'order', 'name']


class MemberApplication(NameParts, models.Model):
    # Set when someone applies from their Dojo account; the member made on approval is linked to it.
    account = models.ForeignKey('network.MemberAccount', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='applications')
    # Applying for themselves (rather than, say, their child): the member made on approval is the account holder's own record.
    for_account_holder = models.BooleanField(default=False)

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='applications')
    name = models.CharField(max_length=255)
    first_name = models.CharField(max_length=150, blank=True)
    surname = models.CharField(max_length=150, blank=True)
    preferred_name = models.CharField(max_length=150, blank=True, help_text='What they like to be called, if not their first name')
    pronouns = models.CharField(max_length=30, blank=True, help_text='e.g. she/her, he/him, they/them')
    date_of_birth = models.DateField(null=True, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    county = models.CharField(max_length=100, blank=True)
    postcode = models.CharField(max_length=20, blank=True)
    guardian_name = models.CharField(max_length=255, blank=True)
    guardian_email = models.EmailField(blank=True)
    guardian_phone = models.CharField(max_length=20, blank=True)
    medical_info = models.TextField(blank=True)
    emergency_contact_name = models.CharField(max_length=255, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)
    emergency_contact_2_name = models.CharField('second emergency contact name', max_length=255, blank=True)
    emergency_contact_2_phone = models.CharField('second emergency contact phone', max_length=20, blank=True)
    notes = models.TextField(blank=True, help_text='Any additional information from the applicant')
    # A promo code entered on the join form (billing/promos.py), given to the member on approval.
    promo = models.ForeignKey('billing.PolicyDiscount', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='applications')
    # Answers to the club's custom fields asked on the join form, {field pk: value}, as on Member.
    custom_field_values = models.JSONField(default=dict, blank=True)
    signature_data = models.TextField(blank=True, help_text='Base64-encoded PNG of drawn signature')
    # Who signed (the applicant, or their parent or guardian), from where, and which of the club's
    # documents they ticked to agree to. The signed copies are made from these on approval.
    signer_name = models.CharField(max_length=255, blank=True)
    signer_ip = models.GenericIPAddressField(null=True, blank=True)
    agreed_waivers = models.JSONField(default=list, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    decided_at = models.DateTimeField(null=True, blank=True, help_text='When the application was approved or rejected')

    def custom_answers(self):
        """[(field name, answer)] for the club's custom fields answered on the join form."""
        answers = []
        for field in self.organisation.custom_fields.filter(pk__in=[k for k in self.custom_field_values if str(k).isdigit()]):
            value = self.custom_field_values.get(str(field.pk))
            if field.field_type == CustomField.FieldType.BOOLEAN:
                value = 'Yes' if value else 'No'
            answers.append((field.name, value or '—'))
        return answers

    def __str__(self):
        return f"{self.name} — {self.organisation} ({self.get_status_display()})"

    class Meta:
        ordering = ['-submitted_at']


class FamilyGroup(models.Model):
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='family_groups')
    name = models.CharField(max_length=255, help_text='e.g. Smith Family')
    discount_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                               help_text='% discount applied to every member in this group')
    members = models.ManyToManyField('Member', through='FamilyGroupMember', related_name='family_groups')

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['organisation', 'name']
        verbose_name = 'Family group'


class FamilyGroupMember(models.Model):
    family_group = models.ForeignKey(FamilyGroup, on_delete=models.CASCADE, related_name='memberships')
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='family_memberships')

    def __str__(self):
        return f"{self.member} in {self.family_group}"

    class Meta:
        unique_together = ('family_group', 'member')


def request_to_leave(member, reason=''):
    """Flag that the member plans to stop training, for the club's staff to follow up. Returns the
    pending request (an existing one if they've already asked)."""
    pending = member.leaving_requests.filter(status=MemberLeavingRequest.Status.PENDING).first()
    return pending or MemberLeavingRequest.objects.create(member=member, reason=(reason or '').strip())


def current_plans(member):
    """The billing policies the member is on: their own, or else those of the classes they're enrolled in."""
    if not member.organisation.has_feature('billing'):
        return []
    if member.billing_policy_id:
        return [member.billing_policy]
    from billing.models import BillingPolicy
    return list(BillingPolicy.objects.filter(classes__enrolments__member=member).distinct().order_by('name'))


class MemberLeavingRequest(models.Model):
    """
    A member's self-reported intention to stop training, submitted from the
    member portal. This does NOT archive the member automatically — it's
    flagged here for staff to review and action from the staff side, so an
    accidental click (or a request that needs a follow-up conversation)
    doesn't silently remove someone.
    """
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending review'
        ACTIONED = 'actioned', 'Archived'
        DISMISSED = 'dismissed', 'Dismissed'

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='leaving_requests')
    reason = models.TextField(blank=True)
    requested_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    def __str__(self):
        return f"{self.member} — leaving request ({self.get_status_display()})"

    class Meta:
        ordering = ['-requested_at']


class MemberNote(models.Model):
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='notes')
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Note on {self.member} by {self.author}"

    class Meta:
        ordering = ['-created_at']
