from organisations.models import Organisation


class OrgSessionTimeoutMiddleware:
    """
    Logs staff out after the organisation's chosen period of inactivity
    (Settings → Security). Every request to an organisation's pages restarts
    the clock, so it measures time since the last request, not since login.
    Pages outside an organisation (login, setup) leave the session alone.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        # Returning nothing lets the view run as normal.
        org_slug = view_kwargs.get('org_slug')
        if not org_slug or not request.user.is_authenticated:
            return
        org = Organisation.objects.filter(slug=org_slug).only('settings').first()
        if org is None:
            return
        minutes = org.session_timeout_minutes
        # 0 = never: None falls back to SESSION_COOKIE_AGE.
        request.session.set_expiry(minutes * 60 if minutes else None)
