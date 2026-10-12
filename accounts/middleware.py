from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import urlencode

from .policy import device_for, is_verified, two_factor_required


class TwoFactorMiddleware:
    """
    After the password, staff with an authenticator must enter a code before
    reaching any page; staff a club requires to use one are sent to set it up.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not self._exempt(request.path):
            device = device_for(request.user)
            if device is not None and not is_verified(request, device):
                return self._send_to(request, 'two_factor_verify')
            if device is None and two_factor_required(request.user):
                return self._send_to(request, 'two_factor_setup')
        return self.get_response(request)

    @staticmethod
    def _exempt(path):
        prefixes = ['/2fa/', reverse('logout'), settings.MEDIA_URL, '/' + settings.STATIC_URL.lstrip('/')]
        return any(path.startswith(prefix) for prefix in prefixes)

    @staticmethod
    def _send_to(request, url_name):
        url = reverse(url_name)
        if request.method == 'GET':
            url += '?' + urlencode({'next': request.get_full_path()})
        if request.headers.get('HX-Request'):
            response = HttpResponse(status=204)
            response['HX-Redirect'] = url
            return response
        return redirect(url)
