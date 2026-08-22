from django.contrib import admin
from .models import (
    Assessment, AssessmentAttendance, AssessmentSession,
    MemberProgression, ProgressionStage, ProgressionSystem,
)


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


class AssessmentSessionInline(admin.TabularInline):
    model = AssessmentSession
    extra = 0


@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'organisation', 'sessions_count', 'members_count')
    list_filter = ('organisation',)
    search_fields = ('name', 'organisation__name')
    inlines = [AssessmentSessionInline]


@admin.register(AssessmentSession)
class AssessmentSessionAdmin(admin.ModelAdmin):
    list_display = ('assessment', 'name', 'date', 'is_cancelled')
    list_filter = ('assessment__organisation', 'is_cancelled')
    date_hierarchy = 'date'


@admin.register(AssessmentAttendance)
class AssessmentAttendanceAdmin(admin.ModelAdmin):
    list_display = ('member', 'session', 'present', 'new_stage')
    list_filter = ('session__assessment__organisation', 'present')
    search_fields = ('member__name',)
