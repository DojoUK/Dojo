"""
JSON signup API, for a club's own website to take membership applications.

    GET  /api/join/<org>/   club name, active waivers and collected fields, to build the form
    POST /api/join/<org>/   create a MemberApplication (JSON or form-encoded)

Cross-origin access is limited to SIGNUP_API_ORIGINS (see origin_allowed):
a request carrying any other Origin is refused, so another site can't post
applications from a visitor's browser. The endpoint is CSRF-exempt because it uses no cookies
or session; it can do only what the public /join/ page does. A honeypot
field and a per-IP rate limit keep bots out.
"""
import json

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .signup import (
    active_waivers,
    client_ip,
    collected_fields,
    create_application,
    origin_allowed,
    signup_org,
)

# A hidden form field people never fill in; bots that fill every input do.
HONEYPOT_FIELD = 'website'


def _cors(response, origin):
    if origin:
        response['Access-Control-Allow-Origin'] = origin
        response['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response['Access-Control-Allow-Headers'] = 'Content-Type'
        response['Access-Control-Max-Age'] = '86400'
        response['Vary'] = 'Origin'
    return response


def _rate_limited(org, request):
    """Count this attempt; True once the address has used up its window."""
    key = f'signup-api:{org.pk}:{client_ip(request)}'
    attempts = cache.get_or_set(key, 0, settings.SIGNUP_API_RATE_WINDOW)
    if attempts >= settings.SIGNUP_API_RATE_LIMIT:
        return True
    try:
        cache.incr(key)
    except ValueError:  # the window expired between the read and the increment
        cache.set(key, 1, settings.SIGNUP_API_RATE_WINDOW)
    return False


@method_decorator(csrf_exempt, name='dispatch')
class SignupApiView(View):
    def dispatch(self, request, *args, **kwargs):
        origin = request.headers.get('Origin')
        if origin and not origin_allowed(origin, kwargs.get('org_slug', '')):
            return JsonResponse({'errors': ['Origin not allowed.']}, status=403)
        return _cors(super().dispatch(request, *args, **kwargs), origin)

    def options(self, request, org_slug):
        return HttpResponse(status=204)

    def get(self, request, org_slug):
        org = signup_org(org_slug)
        return JsonResponse({
            'organisation': org.name,
            'waivers': [
                {
                    'name': w.name,
                    'description': w.description,
                    'url': request.build_absolute_uri(w.file.url) if w.file else None,
                    'required': w.is_required,
                }
                for w in active_waivers(org)
            ],
            'fields': collected_fields(org),
        })

    def post(self, request, org_slug):
        org = signup_org(org_slug)

        if request.content_type == 'application/json':
            try:
                data = json.loads(request.body or b'{}')
            except ValueError:
                return JsonResponse({'errors': ['Invalid JSON.']}, status=400)
            if not isinstance(data, dict):
                return JsonResponse({'errors': ['Invalid JSON.']}, status=400)
        else:
            data = request.POST

        if data.get(HONEYPOT_FIELD):
            # Look successful so the bot moves on, but store nothing.
            return JsonResponse({'ok': True}, status=201)

        if _rate_limited(org, request):
            return JsonResponse(
                {'errors': ['Too many applications from this address. Please try again later.']}, status=429,
            )

        _, errors = create_application(org, data)
        if errors:
            return JsonResponse({'errors': errors}, status=400)
        return JsonResponse({'ok': True}, status=201)
