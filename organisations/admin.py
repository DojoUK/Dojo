from django.contrib import admin
from .models import Organisation, OrganisationMember


@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'subscription_tier', 'created_at')
    list_filter = ('subscription_tier',)
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(OrganisationMember)
class OrganisationMemberAdmin(admin.ModelAdmin):
    list_display = ('user', 'organisation', 'role')
    list_filter = ('role', 'organisation')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'organisation__name')
