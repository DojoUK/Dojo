from django.db import models
from django.utils import timezone
from organisations.models import Organisation
from members.models import Member


class BillingPolicy(models.Model):
    class BillingCycle(models.TextChoices):
        MONTHLY = 'monthly', 'Monthly'
        TERMLY = 'termly', 'Termly'
        ANNUAL = 'annual', 'Annual'
        CUSTOM = 'custom', 'Custom / Manual'

    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='billing_policies')
    name = models.CharField(max_length=255)
    billing_cycle = models.CharField(max_length=20, choices=BillingCycle.choices)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} (£{self.amount}/{self.get_billing_cycle_display()})"

    class Meta:
        ordering = ['organisation', 'name']
        verbose_name = 'Billing policy'
        verbose_name_plural = 'Billing policies'


class OrgTerm(models.Model):
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='terms')
    name = models.CharField(max_length=255, help_text='e.g. Autumn Term 2025')
    start_date = models.DateField()
    end_date = models.DateField()

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['organisation', '-start_date']
        verbose_name = 'Term'


class PolicyDiscount(models.Model):
    class DiscountType(models.TextChoices):
        PERCENTAGE = 'percentage', 'Percentage (%)'
        FIXED = 'fixed', 'Fixed amount (£)'

    policy = models.ForeignKey(BillingPolicy, on_delete=models.CASCADE, related_name='discounts')
    name = models.CharField(max_length=255)
    discount_type = models.CharField(max_length=20, choices=DiscountType.choices)
    value = models.DecimalField(max_digits=8, decimal_places=2)
    auto_apply = models.BooleanField(default=False, help_text='Automatically apply to all new members on this policy')

    def __str__(self):
        if self.discount_type == self.DiscountType.PERCENTAGE:
            return f"{self.name} ({self.value}% off)"
        return f"{self.name} (£{self.value} off)"

    def amount_off(self, base_amount):
        if self.discount_type == self.DiscountType.PERCENTAGE:
            return round(base_amount * self.value / 100, 2)
        return min(self.value, base_amount)

    class Meta:
        ordering = ['policy', 'name']
        verbose_name = 'Policy discount'


class MemberDiscount(models.Model):
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='discounts')
    discount = models.ForeignKey(PolicyDiscount, on_delete=models.CASCADE, related_name='member_discounts')
    is_active = models.BooleanField(default=True)
    applied_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.member} — {self.discount}"

    class Meta:
        unique_together = ('member', 'discount')
        verbose_name = 'Member discount'


class Invoice(models.Model):
    class Status(models.TextChoices):
        UNPAID = 'unpaid', 'Unpaid'
        PAID = 'paid', 'Paid'
        OVERDUE = 'overdue', 'Overdue'

    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='invoices')
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='invoices')
    billing_policy = models.ForeignKey(BillingPolicy, null=True, blank=True, on_delete=models.SET_NULL, related_name='invoices')
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    period = models.CharField(max_length=50, help_text='e.g. January 2026 or Autumn Term 2025')
    due_date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UNPAID)
    notes = models.TextField(blank=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.member} — {self.period} — £{self.amount} ({self.get_status_display()})"

    @property
    def is_overdue(self):
        from datetime import date
        return self.status == self.Status.UNPAID and self.due_date < date.today()

    class Meta:
        ordering = ['-created_at']


class Payment(models.Model):
    class Method(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        STRIPE = 'stripe', 'Stripe'
        BACS = 'bacs', 'BACS transfer'
        CASH = 'cash', 'Cash'

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='payments')
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.MANUAL)
    stripe_payment_id = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    paid_at = models.DateTimeField()
    notes = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.invoice} — £{self.amount} at {self.paid_at}"

    class Meta:
        ordering = ['-paid_at']
