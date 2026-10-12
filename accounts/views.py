from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from . import totp
from .models import TwoFactorDevice, remove_two_factor
from .policy import device_for, is_verified, mark_verified, two_factor_required

SETUP_SECRET_SESSION_KEY = 'two_factor_setup_secret'


def _next_url(request):
    url = request.POST.get('next') or request.GET.get('next') or ''
    if url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return url
    return reverse('root')


def _issuer(user):
    membership = user.organisation_memberships.select_related('organisation').first()
    return f'Dojo ({membership.organisation.name})' if membership else 'Dojo'


class VerifyView(LoginRequiredMixin, View):
    """The second step of signing in: a code from the app, or a recovery code."""
    template_name = 'accounts/verify.html'

    def get(self, request):
        device = device_for(request.user)
        if device is None or is_verified(request, device):
            return redirect(_next_url(request))
        return render(request, self.template_name, {'next': _next_url(request), 'device': device})

    def post(self, request):
        device = device_for(request.user)
        if device is None or is_verified(request, device):
            return redirect(_next_url(request))
        method = device.verify(request.POST.get('code', ''))
        if method is None:
            error = ('Too many wrong codes. Wait five minutes and try again.' if device.is_locked
                     else 'That code didn\'t work. Check your app and try again.')
            return render(request, self.template_name, {
                'next': _next_url(request), 'device': device, 'error': error,
            }, status=400)
        mark_verified(request, device)
        if method == 'recovery':
            left = device.recovery_codes_left
            messages.warning(request, f'You signed in with a recovery code; {left} left. '
                                      'Make new ones from your account page if you\'re running low.')
        return redirect(_next_url(request))


class SetupView(LoginRequiredMixin, View):
    """Link an authenticator app: scan the QR code, then prove it works with one code."""
    template_name = 'accounts/setup.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and device_for(request.user) is not None:
            return redirect(_next_url(request))
        return super().dispatch(request, *args, **kwargs)

    def _context(self, request, secret, **extra):
        uri = totp.provisioning_uri(secret, request.user.username, _issuer(request.user))
        return {
            'qr_svg': totp.qr_svg(uri),
            'secret': totp.grouped(secret),
            'next': _next_url(request),
            'required': two_factor_required(request.user),
            **extra,
        }

    def get(self, request):
        secret = request.session.get(SETUP_SECRET_SESSION_KEY) or totp.generate_secret()
        request.session[SETUP_SECRET_SESSION_KEY] = secret
        return render(request, self.template_name, self._context(request, secret))

    def post(self, request):
        secret = request.session.get(SETUP_SECRET_SESSION_KEY)
        if not secret:
            return redirect(request.get_full_path())
        step = totp.matching_step(secret, request.POST.get('code', ''))
        if step is None:
            return render(request, self.template_name, self._context(
                request, secret, error='That code didn\'t match. Make sure you scanned this page\'s QR code, then try the newest code.',
            ), status=400)
        device = TwoFactorDevice.objects.create(user=request.user, secret=secret, last_used_step=step)
        del request.session[SETUP_SECRET_SESSION_KEY]
        mark_verified(request, device)
        return render(request, 'accounts/recovery_codes.html', {
            'codes': device.new_recovery_codes(), 'next': _next_url(request), 'just_set_up': True,
        })


class ConfirmWithCodeView(LoginRequiredMixin, View):
    """Changes to two-step sign-in itself need a fresh code, so an unattended session can't make them."""
    template_name = 'accounts/confirm.html'
    title = ''
    explanation = ''
    button = ''

    def get(self, request):
        device = device_for(request.user)
        if device is None:
            return redirect(_next_url(request))
        refusal = self.refusal(request)
        return render(request, self.template_name, {
            'title': self.title, 'explanation': self.explanation, 'button': self.button,
            'next': _next_url(request), 'refusal': refusal,
        })

    def post(self, request):
        device = device_for(request.user)
        if device is None or self.refusal(request):
            return redirect(_next_url(request))
        if device.verify(request.POST.get('code', '')) is None:
            return render(request, self.template_name, {
                'title': self.title, 'explanation': self.explanation, 'button': self.button,
                'next': _next_url(request),
                'error': 'Too many wrong codes. Wait five minutes and try again.' if device.is_locked
                         else 'That code didn\'t work.',
            }, status=400)
        return self.confirmed(request, device)

    def refusal(self, request):
        return ''


class RecoveryCodesView(ConfirmWithCodeView):
    title = 'Make new recovery codes'
    explanation = 'Your old recovery codes will stop working.'
    button = 'Make new codes'

    def confirmed(self, request, device):
        return render(request, 'accounts/recovery_codes.html', {
            'codes': device.new_recovery_codes(), 'next': _next_url(request),
        })


class DisableView(ConfirmWithCodeView):
    title = 'Turn off two-step sign-in'
    explanation = 'You\'ll sign in with just your password.'
    button = 'Turn off'

    def refusal(self, request):
        if two_factor_required(request.user):
            return 'Your club requires two-step sign-in, so it can\'t be turned off. To move it to a new phone, ask an admin to reset it.'
        return ''

    def confirmed(self, request, device):
        remove_two_factor(request.user)
        messages.success(request, 'Two-step sign-in is off.')
        return redirect(_next_url(request))
