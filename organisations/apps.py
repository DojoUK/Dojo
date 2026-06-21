from django.apps import AppConfig


class OrganisationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'organisations'

    def ready(self):
        from auditlog.registry import auditlog
        from .models import Organisation, OrganisationMember
        auditlog.register(Organisation)
        auditlog.register(OrganisationMember)
