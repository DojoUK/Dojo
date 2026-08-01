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

    class PricingModel(models.TextChoices):
        FLAT = 'flat', 'Flat rate'
        PER_SESSION = 'per_session', 'Per session'

    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='billing_policies')
    name = models.CharField(max_length=255)
    billing_cycle = models.CharField(max_length=20, choices=BillingCycle.choices)
    pricing_model = models.CharField(max_length=20, choices=PricingModel.choices, default=PricingModel.FLAT)
    # Flat pricing
    amount = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    # Per-session pricing
    per_session_rate = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
                                           help_text='Rate per session for the first enrolled class')
    additional_class_discount = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                                    help_text='% off per_session_rate for 2nd+ enrolled classes (e.g. 50 = half price)')
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        if self.pricing_model == self.PricingModel.PER_SESSION:
            return f"{self.name} (£{self.per_session_rate}/session / {self.get_billing_cycle_display()})"
        return f"{self.name} (£{self.amount} flat / {self.get_billing_cycle_display()})"

    def display_amount(self):
        if self.pricing_model == self.PricingModel.PER_SESSION:
            return f"£{self.per_session_rate}/session"
        return f"£{self.amount}"

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

    @property
    def items_total(self):
        from decimal import Decimal
        return sum((item.line_total for item in self.items.all()), Decimal('0'))

    class Meta:
        ordering = ['-created_at']


class InvoiceItem(models.Model):
    """A product sold on an invoice (e.g. a gi in a specific size). `unit_price` and `description`
    are snapshotted at the time of sale so historical invoices don't change if the product's
    catalogue price or name changes later. Deleting a variant that has been sold is blocked
    (PROTECT) so past invoices always keep an accurate record of what was sold."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    variant = models.ForeignKey('inventory.ProductVariant', on_delete=models.PROTECT, related_name='invoice_items')
    description = models.CharField(max_length=255, blank=True, help_text='Snapshot of the product/size at time of sale')
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=8, decimal_places=2, help_text='Snapshot of the price at time of sale')

    def __str__(self):
        return f"{self.quantity} x {self.description or self.variant} — £{self.line_total}"

    @property
    def line_total(self):
        return (self.unit_price or 0) * self.quantity

    def save(self, *args, **kwargs):
        if not self.description and self.variant_id:
            self.description = f"{self.variant.product.name} — {self.variant.size}"
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['pk']


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
