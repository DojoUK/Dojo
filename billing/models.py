from django.conf import settings
from django.core.validators import MinValueValidator
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
    amount = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
                                 validators=[MinValueValidator(0)])
    # Per-session pricing
    per_session_rate = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
                                           help_text='Rate per session for the first enrolled class')
    additional_class_discount = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                                    help_text='% off per_session_rate for 2nd+ enrolled classes (e.g. 50 = half price)')
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Booking plan: what the policy allows a member to book from the member portal.
    class LimitPeriod(models.TextChoices):
        WEEK = 'week', 'a week'
        MONTH = 'month', 'a month'

    # A plan limits either sessions (so many a week or month) or classes (enrolled on at once).
    class LimitType(models.TextChoices):
        SESSIONS = 'sessions', 'Sessions'
        CLASSES = 'classes', 'Classes'

    member_selectable = models.BooleanField(
        default=False,
        help_text='Members can pick this as their plan from the Book page of the member portal.',
    )
    plan_description = models.TextField(
        blank=True, help_text='Shown to members when they choose a plan (the description above is internal).',
    )
    is_family_plan = models.BooleanField(
        default=False,
        help_text='One plan for a whole family (members linked in a family group). A flat price is charged '
                  'once per family, per-session prices add up every child, and any session limit is shared.',
    )
    max_family_members = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(2)],
        help_text='Family plans only: the most family members the plan can cover. Blank for no limit.',
    )
    session_limit = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)],
        help_text='The most sessions a member on this policy can be booked into in each period. Blank means unlimited.',
    )
    limit_period = models.CharField(max_length=10, choices=LimitPeriod.choices, default=LimitPeriod.MONTH)
    limit_type = models.CharField(
        max_length=10, choices=LimitType.choices, default=LimitType.SESSIONS,
        help_text='Limit sessions (session_limit, each week or month) or classes (class_limit, enrolled on at once).',
    )
    class_limit = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)],
        help_text='The most classes a member on this policy can be enrolled on at once. Single-session '
                  'bookings don\'t count. Blank means unlimited.',
    )

    @property
    def limits_classes(self):
        return self.limit_type == self.LimitType.CLASSES and bool(self.class_limit)

    @property
    def limits_sessions(self):
        return self.limit_type != self.LimitType.CLASSES and bool(self.session_limit)

    def limit_text(self):
        """'Unlimited sessions', or e.g. '2 sessions a week' or 'Up to 2 classes at a time'."""
        if self.limits_classes:
            return f"Up to {self.class_limit} class{'es' if self.class_limit != 1 else ''} at a time"
        if not self.limits_sessions:
            return 'Unlimited sessions'
        return f"{self.session_limit} session{'s' if self.session_limit != 1 else ''} {self.get_limit_period_display()}"

    def __str__(self):
        if self.pricing_model == self.PricingModel.PER_SESSION:
            return f"{self.name} (£{self.per_session_rate}/session / {self.get_billing_cycle_display()})"
        return f"{self.name} (£{self.amount} flat / {self.get_billing_cycle_display()})"

    def price_summary(self):
        """For members: '£40 a month', '£5 a session', or just the price for custom billing."""
        if self.pricing_model == self.PricingModel.PER_SESSION:
            return f"£{self.per_session_rate} a session" if self.per_session_rate is not None else ''
        if self.amount is None:
            return ''
        per = {self.BillingCycle.MONTHLY: ' a month', self.BillingCycle.TERMLY: ' a term',
               self.BillingCycle.ANNUAL: ' a year'}.get(self.billing_cycle, '')
        return f"£{self.amount}{per}"

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
    value = models.DecimalField(max_digits=8, decimal_places=2, validators=[MinValueValidator(0)])
    # Applied at billing to everyone billed on the plan (billing/calculator.py), unless staff exempt a member
    # on their page, which records an inactive MemberDiscount.
    auto_apply = models.BooleanField(default=False, help_text='Applied to everyone billed on this plan, unless exempted')
    # Promo codes and intro offers (billing/promos.py): a code people type on the join form or their
    # portal's Plans page to get this discount, with optional limits.
    code = models.CharField(max_length=30, blank=True, help_text='Stored in capitals; unique within the club')
    new_members_only = models.BooleanField(default=False, help_text='Only for members who have never been invoiced')
    valid_until = models.DateField(null=True, blank=True, help_text='Last day the code can be used')
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    duration_months = models.PositiveSmallIntegerField(null=True, blank=True,
                                                       help_text='How long it lasts once applied; blank for as long as they\'re on the plan')
    show_publicly = models.BooleanField(default=False, help_text='Show as an intro offer on Find clubs and the join page')

    @property
    def uses(self):
        return self.member_discounts.count()

    def offer_text(self):
        """For intro offers: e.g. '50% off Adult unlimited for 1 month'."""
        off = f'{self.value.normalize():f}% off' if self.discount_type == self.DiscountType.PERCENTAGE else f'£{self.value} off'
        months = self.duration_months
        length = f' for {months} month{"s" if months != 1 else ""}' if months else ''
        return f'{off} {self.policy.name}{length}'

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
    # A discount that lasts a set time (PolicyDiscount.duration_months) stops applying after this day.
    expires_on = models.DateField(null=True, blank=True)

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
        CANCELLED = 'cancelled', 'Cancelled'

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
    overpayment_credited = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text="How much of this invoice's overpayment (if any) has already been "
                  "pulled into a later invoice for the same member as a discount.",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    cancel_reason = models.TextField(blank=True, help_text='Why this invoice was cancelled (e.g. issued in error).')
    created_at = models.DateTimeField(auto_now_add=True)

    class AutoPayStatus(models.TextChoices):
        NONE = '', 'Not collected automatically'
        DUE = 'due', 'Waiting to be collected'
        COLLECTING = 'collecting', 'Being collected'
        PROCESSING = 'processing', 'Direct Debit in progress'
        PAID = 'paid', 'Collected'
        FAILED = 'failed', 'Collection failed'

    # The automatic run that made this invoice, if any (see InvoiceSchedule).
    run = models.ForeignKey('InvoiceRun', null=True, blank=True, on_delete=models.SET_NULL, related_name='invoices')
    autopay_status = models.CharField(max_length=12, choices=AutoPayStatus.choices, blank=True, default='')
    autopay_payment_intent = models.CharField(max_length=255, blank=True)
    autopay_error = models.CharField(max_length=255, blank=True)
    autopay_attempts = models.PositiveSmallIntegerField(default=0)

    def __str__(self):
        return f"{self.member} — {self.period} — £{self.amount} ({self.get_status_display()})"

    @property
    def is_cancelled(self):
        return self.status == self.Status.CANCELLED

    @property
    def is_overdue(self):
        from datetime import date
        return self.status == self.Status.UNPAID and self.due_date < date.today()

    @property
    def amount_paid(self):
        from decimal import Decimal
        return sum((payment.amount for payment in self.payments.all()), Decimal('0'))

    @property
    def balance_due(self):
        """What's left to pay: the amount less any payments already recorded."""
        from decimal import Decimal
        if self.status in (self.Status.PAID, self.Status.CANCELLED):
            return Decimal('0')
        return max(Decimal(self.amount) - self.amount_paid, Decimal('0'))

    @property
    def is_partial(self):
        """Some money has come in, but not enough to cover the invoice yet."""
        from decimal import Decimal
        return self.status != self.Status.PAID and Decimal('0') < self.amount_paid < Decimal(self.amount)

    @property
    def overpaid_amount(self):
        """How much more than the invoice total has been paid in, if any."""
        from decimal import Decimal
        return max(self.amount_paid - Decimal(self.amount), Decimal('0'))

    @property
    def available_credit(self):
        """Overpayment on this invoice not yet taken off a later invoice."""
        from decimal import Decimal
        return max(self.overpaid_amount - Decimal(self.overpayment_credited), Decimal('0'))

    @property
    def payment_reference(self):
        """The reference members quote on a bank transfer, e.g. INV1234."""
        settings = getattr(self.organisation, 'payment_settings', None)
        prefix = settings.reference_prefix if settings else 'INV'
        return f'{prefix}{self.pk}'

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


class PaymentDispute(models.Model):
    """
    A chargeback: a member asked their bank to reverse a Stripe payment. Stripe takes the money (and
    its dispute fee) from the club's balance while the club sends evidence on Stripe's dashboard,
    then the bank decides. Kept up to date from Stripe's charge.dispute.* webhook events.
    """
    STATUS_TEXT = {
        'warning_needs_response': 'Bank enquiry: needs a response', 'warning_under_review': 'Bank enquiry: with the bank',
        'warning_closed': 'Bank enquiry closed', 'needs_response': 'Needs a response',
        'under_review': 'With the bank', 'won': 'Won', 'lost': 'Lost', 'prevented': 'Prevented',
    }
    REASON_TEXT = {
        'fraudulent': "Says they didn't make the payment", 'unrecognized': "Doesn't recognise the payment",
        'duplicate': 'Says they were charged twice', 'subscription_canceled': 'Says they had cancelled',
        'product_not_received': "Says they didn't get what they paid for",
        'product_unacceptable': "Unhappy with what they paid for",
        'credit_not_processed': 'Says they were promised a refund', 'general': 'No reason given',
    }
    NEEDS_RESPONSE = ('needs_response', 'warning_needs_response')
    OPEN = NEEDS_RESPONSE + ('under_review', 'warning_under_review')

    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='payment_disputes')
    invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, null=True, blank=True, related_name='disputes')
    payment = models.ForeignKey('Payment', on_delete=models.SET_NULL, null=True, blank=True, related_name='disputes')
    stripe_dispute_id = models.CharField(max_length=255, unique=True)
    payment_intent = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    reason = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=30)
    evidence_due_by = models.DateTimeField(null=True, blank=True)
    livemode = models.BooleanField(default=False)
    opened_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)
    # A lost dispute: the money went back to the member, recorded against the invoice as a minus payment.
    chargeback_payment = models.OneToOneField('Payment', on_delete=models.SET_NULL, null=True, blank=True,
                                              related_name='chargeback_for')

    class Meta:
        ordering = ['-opened_at']

    def __str__(self):
        return f'Dispute {self.stripe_dispute_id}: £{self.amount} ({self.status})'

    @property
    def is_open(self):
        return self.status in self.OPEN

    @property
    def needs_response(self):
        return self.status in self.NEEDS_RESPONSE

    @property
    def is_enquiry(self):
        return self.status.startswith('warning_')

    @property
    def status_text(self):
        return self.STATUS_TEXT.get(self.status, self.status.replace('_', ' ').capitalize())

    @property
    def reason_text(self):
        return self.REASON_TEXT.get(self.reason, self.reason.replace('_', ' ').capitalize())

    @property
    def member(self):
        return self.invoice.member if self.invoice_id else None

    @property
    def stripe_url(self):
        """Where the club answers it: the dispute on Stripe's dashboard."""
        return f"https://dashboard.stripe.com/{'' if self.livemode else 'test/'}disputes/{self.stripe_dispute_id}"


class PaymentSettings(models.Model):
    """How members can pay: chosen in the setup wizard, changed in Settings → Payments."""
    organisation = models.OneToOneField(Organisation, on_delete=models.CASCADE, related_name='payment_settings')
    accept_bank_transfer = models.BooleanField(default=False)
    accept_card = models.BooleanField(default=False)
    accept_cash = models.BooleanField(default=False)
    bank_account_name = models.CharField(max_length=100, blank=True)
    bank_sort_code = models.CharField(max_length=8, blank=True)
    bank_account_number = models.CharField(max_length=8, blank=True)
    reference_prefix = models.CharField(max_length=10, default='INV',
                                        help_text='Payment references are this followed by the invoice number.')
    instructions = models.TextField(blank=True, help_text='Anything else members should know, shown with every invoice.')
    stripe_publishable_key = models.CharField(max_length=255, blank=True)

    class AutoPayPolicy(models.TextChoices):
        OFF = 'off', 'Off'
        OPTIONAL = 'optional', 'Optional: members choose'
        ENCOURAGED = 'encouraged', 'Expected: members are asked to set it up'

    auto_pay = models.CharField(max_length=12, choices=AutoPayPolicy.choices, default=AutoPayPolicy.OFF)
    # The Stripe secret key and webhook signing secret, encrypted (see mailer.crypto).
    secrets = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'payment settings'

    def __str__(self):
        return f'Payments for {self.organisation}'

    def get_secrets(self):
        from mailer import crypto
        return crypto.decrypt_dict(self.secrets)

    def set_secrets(self, values):
        from mailer import crypto
        self.secrets = crypto.encrypt_dict({k: v for k, v in values.items() if v})

    @property
    def stripe_secret_key(self):
        return self.get_secrets().get('stripe_secret_key', '')

    @property
    def stripe_webhook_secret(self):
        return self.get_secrets().get('stripe_webhook_secret', '')

    @property
    def stripe_webhook_needs_update(self):
        """True when the webhook was set up before Dojo listened for all the events it uses now."""
        from .stripe_setup import WEBHOOK_EVENTS
        secrets = self.get_secrets()
        return bool(secrets.get('stripe_webhook_secret')) and secrets.get('stripe_webhook_events', '').split() != WEBHOOK_EVENTS

    @property
    def stripe_test_mode(self):
        return self.stripe_publishable_key.startswith('pk_test_')


class AutoPay(models.Model):
    """A member's saved card or Direct Debit, charged when an invoice is made for them."""
    member = models.OneToOneField(Member, on_delete=models.CASCADE, related_name='autopay')
    payment_method_id = models.CharField(max_length=255)
    mandate_id = models.CharField(max_length=255, blank=True)
    method_type = models.CharField(max_length=32, help_text='Stripe payment method type, e.g. card or bacs_debit')
    label = models.CharField(max_length=100, help_text='e.g. Visa ending 4242')
    active = models.BooleanField(default=True)
    set_up_at = models.DateTimeField(auto_now=True)
    # Cards only: the last day the card works (the end of its expiry month), and the expiry date the
    # member was last warned about, so each card is warned about once (billing.autopay.warn_expiring_cards).
    expires_on = models.DateField(null=True, blank=True)
    expiry_warned_for = models.DateField(null=True, blank=True)

    EXPIRY_WARNING_DAYS = 30

    def __str__(self):
        return f'{self.member}: {self.label}'

    def expiry_state(self, today=None):
        """'expired', 'soon' (within EXPIRY_WARNING_DAYS) or '' for a card that's fine or isn't a card."""
        if self.expires_on is None:
            return ''
        from datetime import timedelta

        from django.utils import timezone
        today = today or timezone.localdate()
        if self.expires_on < today:
            return 'expired'
        return 'soon' if self.expires_on <= today + timedelta(days=self.EXPIRY_WARNING_DAYS) else ''


class InvoiceSchedule(models.Model):
    """When Dojo makes a plan's invoices by itself. The scheduler container runs it."""
    class SendMode(models.TextChoices):
        REVIEW = 'review', 'Let me check them first'
        SEND = 'send', 'Send them automatically'

    policy = models.OneToOneField(BillingPolicy, on_delete=models.CASCADE, related_name='schedule')
    enabled = models.BooleanField(default=False)
    # Monthly plans: the day of the month invoices are made (1-28).
    day_of_month = models.PositiveSmallIntegerField(default=1)
    # Termly plans: how many days before each term starts.
    days_before_term = models.PositiveSmallIntegerField(default=7)
    # Annual plans: the date each year.
    annual_month = models.PositiveSmallIntegerField(default=9)
    annual_day = models.PositiveSmallIntegerField(default=1)
    due_after_days = models.PositiveSmallIntegerField(default=14)
    send_mode = models.CharField(max_length=10, choices=SendMode.choices, default=SendMode.REVIEW)
    # Periods whose invoice date is before this aren't made, so switching on mid-month waits for the next one.
    starts_from = models.DateField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Schedule for {self.policy}'


class InvoiceRun(models.Model):
    """One automatic batch of invoices for a plan and period."""
    class Status(models.TextChoices):
        AWAITING_REVIEW = 'awaiting_review', 'Waiting for you to check'
        SENT = 'sent', 'Sent'
        CANCELLED = 'cancelled', 'Cancelled'

    schedule = models.ForeignKey(InvoiceSchedule, on_delete=models.CASCADE, related_name='runs')
    period = models.CharField(max_length=50)
    status = models.CharField(max_length=20, choices=Status.choices)
    invoice_count = models.PositiveIntegerField(default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['schedule', 'period'], name='one_run_per_plan_and_period')]

    def __str__(self):
        return f'{self.schedule.policy.name}: {self.period}'


class Expense(models.Model):
    """
    A club outgoing (rent, equipment, coach pay and so on), so the finance
    report can show revenue less expenses: profit, month by month.
    """

    class Category(models.TextChoices):
        RENT = 'rent', 'Rent & venue hire'
        UTILITIES = 'utilities', 'Utilities'
        EQUIPMENT = 'equipment', 'Equipment & mats'
        INSURANCE = 'insurance', 'Insurance'
        SALARIES = 'salaries', 'Coach pay & salaries'
        LICENSING = 'licensing', 'Licensing & affiliation fees'
        MARKETING = 'marketing', 'Marketing'
        MAINTENANCE = 'maintenance', 'Maintenance & repairs'
        OTHER = 'other', 'Other'

    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='expenses')
    description = models.CharField(max_length=255)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    expense_date = models.DateField(default=timezone.localdate)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.description} — £{self.amount} ({self.expense_date})"

    class Meta:
        ordering = ['-expense_date', '-created_at']
