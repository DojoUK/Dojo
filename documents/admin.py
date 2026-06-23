from django.contrib import admin
from .models import Document, SignedWaiver, WaiverTemplate


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ['name', 'member', 'category', 'uploaded_at', 'uploaded_by']
    list_filter = ['category']
    search_fields = ['name', 'member__name']


@admin.register(WaiverTemplate)
class WaiverTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'organisation', 'is_required', 'is_active', 'created_at']
    list_filter = ['is_required', 'is_active', 'organisation']
    search_fields = ['name']


@admin.register(SignedWaiver)
class SignedWaiverAdmin(admin.ModelAdmin):
    list_display = ['signer_name', 'template', 'member', 'signed_at', 'offline']
    list_filter = ['template', 'offline']
    search_fields = ['signer_name', 'member__name']
