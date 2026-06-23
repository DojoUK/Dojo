import secrets
from django.contrib.auth.models import User
from django.db import models
from organisations.models import Organisation


def generate_token():
    return secrets.token_urlsafe(32)


class Member(models.Model):
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='club_members')
    name = models.CharField(max_length=255)
    date_of_birth = models.DateField(null=True, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    emergency_contact_name = models.CharField(max_length=255, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)
    emergency_contact_2_name = models.CharField(max_length=255, blank=True)
    emergency_contact_2_phone = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    token = models.CharField(max_length=64, unique=True, default=generate_token)
    joined_date = models.DateField(null=True, blank=True)
    custom_field_values = models.JSONField(default=dict, blank=True)
    monthly_fee = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True)
    subscription_status = models.CharField(max_length=20, blank=True)
    licence_number = models.CharField(max_length=100, blank=True)
    licence_expiry = models.DateField(null=True, blank=True)
    medical_info = models.TextField(blank=True, help_text='Medical conditions, allergies, or other health information coaches should know about')

    @property
    def has_active_subscription(self):
        return self.subscription_status == 'active'

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

    def __str__(self):
        return f"{self.organisation} — {self.name}"

    class Meta:
        ordering = ['organisation', 'order', 'name']


class MemberApplication(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='applications')
    name = models.CharField(max_length=255)
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
    notes = models.TextField(blank=True, help_text='Any additional information from the applicant')
    signature_data = models.TextField(blank=True, help_text='Base64-encoded PNG of drawn signature')
    submitted_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    def __str__(self):
        return f"{self.name} — {self.organisation} ({self.get_status_display()})"

    class Meta:
        ordering = ['-submitted_at']


class MemberNote(models.Model):
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='notes')
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Note on {self.member} by {self.author}"

    class Meta:
        ordering = ['-created_at']
