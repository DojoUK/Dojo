from django.contrib import admin
from .models import Class, ClassCoach, ClassMember, Session, Attendance


class ClassCoachInline(admin.TabularInline):
    model = ClassCoach
    extra = 0


class ClassMemberInline(admin.TabularInline):
    model = ClassMember
    extra = 0


class AttendanceInline(admin.TabularInline):
    model = Attendance
    extra = 0


@admin.register(Class)
class ClassAdmin(admin.ModelAdmin):
    list_display = ('name', 'organisation', 'schedule_display')
    list_filter = ('organisation',)
    search_fields = ('name', 'organisation__name')
    inlines = [ClassCoachInline, ClassMemberInline]


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ('assigned_class', 'date', 'is_cancelled', 'is_extra')
    list_filter = ('assigned_class__organisation', 'assigned_class', 'is_cancelled', 'is_extra')
    date_hierarchy = 'date'
    inlines = [AttendanceInline]
