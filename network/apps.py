from django.apps import AppConfig


class NetworkConfig(AppConfig):
    """The Dojo club network: member accounts that span clubs, favourites, and the club finder."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'network'
    verbose_name = 'Dojo network'
