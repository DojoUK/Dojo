from django.apps import AppConfig


class ClassesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'classes'

    def ready(self):
        from auditlog.registry import auditlog
        from .models import Attendance, Class, ClassCoach, ClassMember, Session
        auditlog.register(Class)
        auditlog.register(ClassCoach)
        auditlog.register(ClassMember)
        auditlog.register(Session)
        auditlog.register(Attendance)
