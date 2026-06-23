from django.contrib import admin
from .models import Member, Guardian, CustomField


class GuardianInline(admin.TabularInline):
    model = Guardian
    extra = 0


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ('name', 'organisation', 'email', 'is_active', 'joined_date')
    list_filter = ('organisation', 'is_active')
    search_fields = ('name', 'email', 'phone')
    readonly_fields = ('token',)
    inlines = [GuardianInline]


@admin.register(CustomField)
class CustomFieldAdmin(admin.ModelAdmin):
    list_display = ('name', 'organisation', 'field_type', 'order')
    list_filter = ('organisation', 'field_type')
    search_fields = ('name', 'organisation__name')
