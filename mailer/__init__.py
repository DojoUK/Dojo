def connection_for(organisation):
    """An email connection that sends with `organisation`'s chosen service (Settings → Email)."""
    from django.core.mail import get_connection
    return get_connection(organisation=organisation)
