from django.apps import AppConfig


class GradingsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'gradings'

    def ready(self):
        from auditlog.registry import auditlog
        from .models import Grading, GradingAttendance, GradingSession
        auditlog.register(Grading)
        auditlog.register(GradingSession)
        auditlog.register(GradingAttendance)
