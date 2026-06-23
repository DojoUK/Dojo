from django.contrib import admin
from .models import Invoice, Payment


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    readonly_fields = ('stripe_payment_id', 'amount', 'paid_at')


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('member', 'organisation', 'period', 'amount', 'status', 'due_date')
    list_filter = ('organisation', 'status')
    search_fields = ('member__name',)
    date_hierarchy = 'due_date'
    inlines = [PaymentInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('invoice', 'amount', 'paid_at')
    search_fields = ('invoice__member__name', 'stripe_payment_id')
    date_hierarchy = 'paid_at'
