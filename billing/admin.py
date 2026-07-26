from django.contrib import admin
from .models import Invoice, Payment, BillingPolicy, OrgTerm, PolicyDiscount, MemberDiscount


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


class PolicyDiscountInline(admin.TabularInline):
    model = PolicyDiscount
    extra = 1


@admin.register(BillingPolicy)
class BillingPolicyAdmin(admin.ModelAdmin):
    list_display = ('name', 'organisation', 'billing_cycle', 'amount', 'is_active')
    list_filter = ('organisation', 'billing_cycle', 'is_active')
    inlines = [PolicyDiscountInline]


@admin.register(OrgTerm)
class OrgTermAdmin(admin.ModelAdmin):
    list_display = ('name', 'organisation', 'start_date', 'end_date')
    list_filter = ('organisation',)


@admin.register(MemberDiscount)
class MemberDiscountAdmin(admin.ModelAdmin):
    list_display = ('member', 'discount', 'is_active', 'applied_at')
    list_filter = ('is_active',)
