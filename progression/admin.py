from django.contrib import admin
from .models import MemberProgression, ProgressionStage, ProgressionSystem


class ProgressionStageInline(admin.TabularInline):
    model = ProgressionStage
    extra = 0


@admin.register(ProgressionSystem)
class ProgressionSystemAdmin(admin.ModelAdmin):
    list_display = ('name', 'organisation', 'assign_to_new_members', 'order')
    list_filter = ('organisation',)
    search_fields = ('name', 'organisation__name')
    inlines = [ProgressionStageInline]


@admin.register(MemberProgression)
class MemberProgressionAdmin(admin.ModelAdmin):
    list_display = ('member', 'stage', 'achieved_date')
    list_filter = ('stage__system__organisation', 'stage__system')
    search_fields = ('member__name',)
    date_hierarchy = 'achieved_date'
