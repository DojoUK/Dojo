from django.contrib import admin
from .models import Grading, GradingAttendance, GradingSession


class GradingSessionInline(admin.TabularInline):
    model = GradingSession
    extra = 0


@admin.register(Grading)
class GradingAdmin(admin.ModelAdmin):
    list_display = ('name', 'organisation', 'sessions_count', 'judoka_count')
    list_filter = ('organisation',)
    search_fields = ('name', 'organisation__name')
    inlines = [GradingSessionInline]


@admin.register(GradingSession)
class GradingSessionAdmin(admin.ModelAdmin):
    list_display = ('grading', 'name', 'date', 'is_cancelled')
    list_filter = ('grading__organisation', 'is_cancelled')
    date_hierarchy = 'date'


@admin.register(GradingAttendance)
class GradingAttendanceAdmin(admin.ModelAdmin):
    list_display = ('member', 'session', 'present', 'new_stage')
    list_filter = ('session__grading__organisation', 'present')
    search_fields = ('member__name',)
