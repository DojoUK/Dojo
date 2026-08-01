from django.conf import settings
from django.db import models, transaction
from django.db.models import Sum

from organisations.models import Organisation


class Product(models.Model):
    """A sellable item, e.g. 'Adult Gi', 'Kids Gi', 'White Belt'. Priced and stocked per-size via ProductVariant."""

    class Category(models.TextChoices):
        GI = 'gi', 'Gi / Uniform'
        BELT = 'belt', 'Belt'
        PROTECTIVE = 'protective', 'Protective gear'
        APPAREL = 'apparel', 'Apparel'
        ACCESSORY = 'accessory', 'Accessory'
        OTHER = 'other', 'Other'

    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='products')
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, help_text='Inactive products are hidden from invoicing but keep their history')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    @property
    def total_in_stock(self):
        return self.variants.aggregate(t=Sum('quantity_in_stock'))['t'] or 0

    @property
    def price_range(self):
        prices = list(self.variants.values_list('price', flat=True))
        if not prices:
            return None
        low, high = min(prices), max(prices)
        return (low, high)

    class Meta:
        ordering = ['organisation', 'name']


class ProductVariant(models.Model):
    """A specific size of a Product. Holds its own price and stock level, since gis are typically
    priced and stocked per-size (a size 000 gi is cheaper and stocked differently to an adult 5)."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='variants')
    size = models.CharField(
        max_length=30,
        help_text='e.g. 000, 0, 1, 2, 3, 4, 5, XS, S, M, L, XL, Child 6, Adult L',
    )
    sku = models.CharField(max_length=64, blank=True)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    quantity_in_stock = models.PositiveIntegerField(default=0)
    low_stock_threshold = models.PositiveIntegerField(
        default=0, help_text='Show a low-stock warning at or below this level (0 = disabled)'
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product.name} — {self.size}"

    @property
    def is_low_stock(self):
        return self.low_stock_threshold > 0 and self.quantity_in_stock <= self.low_stock_threshold

    @property
    def is_in_stock(self):
        return self.quantity_in_stock > 0

    def adjust_stock(self, delta, reason, invoice=None, notes='', user=None):
        """
        Atomically adjust stock level and record a StockMovement.
        `delta` is signed: positive adds stock (restock/return), negative removes it (sale/write-off).
        Locks the row for the duration of the transaction so concurrent sales can't oversell.
        Raises ValueError if the adjustment would take stock below zero.
        """
        with transaction.atomic():
            locked = ProductVariant.objects.select_for_update().get(pk=self.pk)
            new_quantity = locked.quantity_in_stock + delta
            if new_quantity < 0:
                raise ValueError(
                    f"Not enough stock for {locked}: have {locked.quantity_in_stock}, "
                    f"tried to remove {-delta}."
                )
            locked.quantity_in_stock = new_quantity
            locked.save(update_fields=['quantity_in_stock'])
            movement = StockMovement.objects.create(
                variant=locked,
                quantity_change=delta,
                reason=reason,
                invoice=invoice,
                notes=notes,
                created_by=user if (user is not None and getattr(user, 'is_authenticated', False)) else None,
            )
        self.quantity_in_stock = new_quantity
        return movement

    class Meta:
        ordering = ['product', 'size']
        unique_together = ('product', 'size')
        verbose_name = 'Product variant (size)'


class StockMovement(models.Model):
    """Audit trail of every stock change: restocks, manual corrections, and sales via invoices."""

    class Reason(models.TextChoices):
        RESTOCK = 'restock', 'Restock'
        CORRECTION = 'correction', 'Manual correction'
        SALE = 'sale', 'Sale (invoice)'
        RETURN = 'return', 'Return / refund'
        WRITE_OFF = 'write_off', 'Write-off / damaged'

    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name='movements')
    quantity_change = models.IntegerField(help_text='Positive = stock added, negative = stock removed')
    reason = models.CharField(max_length=20, choices=Reason.choices)
    invoice = models.ForeignKey(
        'billing.Invoice', null=True, blank=True, on_delete=models.SET_NULL, related_name='stock_movements'
    )
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        sign = '+' if self.quantity_change > 0 else ''
        return f"{self.variant} {sign}{self.quantity_change} ({self.get_reason_display()})"

    class Meta:
        ordering = ['-created_at']
