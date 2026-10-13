from django.contrib import admin

from .models import (
    Grading,
    GradingCandidate,
    GradingSession,
    MemberProgression,
    ProgressionStage,
    ProgressionSystem,
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


class GradingSessionInline(admin.TabularInline):
    model = GradingSession
    extra = 0


@admin.register(Grading)
class GradingAdmin(admin.ModelAdmin):
    list_display = ('name', 'organisation', 'system', 'created_at')
    list_filter = ('organisation',)
    search_fields = ('name',)
    inlines = (GradingSessionInline,)


@admin.register(GradingCandidate)
class GradingCandidateAdmin(admin.ModelAdmin):
    list_display = ('member', 'session', 'target_stage', 'result')
    list_filter = ('result', 'session__grading__organisation')
    search_fields = ('member__name', 'session__grading__name')
