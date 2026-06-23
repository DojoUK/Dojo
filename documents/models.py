from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from members.models import Member
from organisations.models import Organisation


class Document(models.Model):
    class Category(models.TextChoices):
        CONSENT = 'consent', 'Consent form'
        MEDICAL = 'medical', 'Medical'
        WAIVER = 'waiver', 'Waiver'
        MEMBERSHIP = 'membership', 'Membership agreement'
        OTHER = 'other', 'Other'

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='documents')
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    file = models.FileField(upload_to='documents/%Y/%m/')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.member} — {self.name}"

    class Meta:
        ordering = ['-uploaded_at']


class WaiverTemplate(models.Model):
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='waiver_templates')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    file = models.FileField(upload_to='waiver_templates/')
    is_required = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} — {self.organisation}"

    class Meta:
        ordering = ['name']


class SignedWaiver(models.Model):
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='signed_waivers', null=True, blank=True)
    application = models.ForeignKey(
        'members.MemberApplication', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='signed_waivers'
    )
    template = models.ForeignKey(WaiverTemplate, on_delete=models.PROTECT, related_name='signed_copies')
    signed_pdf = models.FileField(upload_to='signed_waivers/%Y/%m/')
    signer_name = models.CharField(max_length=255)
    signed_at = models.DateTimeField(default=timezone.now)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    offline = models.BooleanField(default=False, help_text='Signed on paper, uploaded manually')

    def __str__(self):
        return f"{self.signer_name} — {self.template.name} ({self.signed_at:%d %b %Y})"

    class Meta:
        ordering = ['-signed_at']
