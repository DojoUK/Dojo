from django.apps import AppConfig


class ProgressionConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'progression'

    def ready(self):
        from auditlog.registry import auditlog
        from .models import MemberProgression, ProgressionStage, ProgressionSystem
        auditlog.register(ProgressionSystem)
        auditlog.register(ProgressionStage)
        auditlog.register(MemberProgression)
