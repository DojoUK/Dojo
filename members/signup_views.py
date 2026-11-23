from django.shortcuts import render
from django.views import View

from .forms import build_custom_field_widgets, extract_custom_field_values
from .names import PRONOUN_SUGGESTIONS
from .signup import active_waivers, application_custom_fields, client_ip, create_application, signup_org


def _member_account(request):
    """The signed-in visitor's Dojo account, if they have one: applications made with it are linked to it."""
    if request.user.is_authenticated:
        return getattr(request.user, 'member_account', None)
    return None


def _account_holder(account):
    """The account holder, for the form to put in as parent or guardian when applying for someone else."""
    return {'name': account.name, 'email': account.email, 'phone': account.phone} if account else None


class SignupView(View):
    def _render(self, request, org, account, data, errors=()):
        fields = application_custom_fields(org)
        answers = extract_custom_field_values(org, data, fields) if errors else {}
        agreed = {key[len('agree_'):] for key in (data or {}) if key.startswith('agree_')} if errors else set()
        return render(request, 'members/signup.html', {
            'org': org, 'waivers': active_waivers(org), 'data': data, 'errors': errors, 'account': account,
            'account_holder': _account_holder(account), 'agreed': agreed, 'pronoun_suggestions': PRONOUN_SUGGESTIONS,
            'custom_fields': build_custom_field_widgets(org, initial_values=answers, fields=fields),
            **self._promo_context(request, org, data),
        })

    def _promo_context(self, request, org, data):
        """The club's intro offers, and whether to ask for a promo code (with one from an offer link filled in)."""
        if not org.has_feature('billing'):
            return {}
        from billing import promos
        from billing.models import PolicyDiscount
        has_codes = PolicyDiscount.objects.filter(policy__organisation=org, policy__is_active=True).exclude(code='').exists()
        code = (data or {}).get('promo_code') or request.GET.get('code', '')
        return {'intro_offers': promos.public_offers(org), 'ask_promo': has_codes, 'promo_code': promos.normalise(code)}

    def get(self, request, org_slug):
        org = signup_org(org_slug)
        account = _member_account(request)
        # Applying from a Dojo account: start with My details (the form shows only what this club collects).
        from network.details import application_initial
        data = application_initial(account) if account else None
        return self._render(request, org, account, data)

    def post(self, request, org_slug):
        org = signup_org(org_slug)
        application, errors = create_application(org, request.POST, require_agreement=True)
        account = _member_account(request)
        if errors:
            return self._render(request, org, account, request.POST, errors)
        if application.agreed_waivers:
            application.signer_ip = client_ip(request) or None
            application.save(update_fields=['signer_ip'])
        if account:
            application.account = account
            application.for_account_holder = request.POST.get('applicant', 'me') != 'other'
            application.save(update_fields=['account', 'for_account_holder'])
            if application.for_account_holder:
                # Anything new or changed here is kept in My details for the next club, and at the others.
                from network.details import save_from_application, sync
                sync(account, save_from_application(account, org, request.POST))
        return render(request, 'members/signup_done.html', {'org': org, 'account': account})
