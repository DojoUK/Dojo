from django.shortcuts import render
from django.views import View

from .signup import active_waivers, create_application, signup_org


class SignupView(View):
    def get(self, request, org_slug):
        org = signup_org(org_slug)
        return render(request, 'members/signup.html', {'org': org, 'waivers': active_waivers(org)})

    def post(self, request, org_slug):
        org = signup_org(org_slug)
        _, errors = create_application(org, request.POST)
        if errors:
            return render(request, 'members/signup.html', {
                'org': org, 'waivers': active_waivers(org), 'errors': errors, 'data': request.POST,
            })
        return render(request, 'members/signup_done.html', {'org': org})
