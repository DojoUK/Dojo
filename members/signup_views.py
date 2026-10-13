from datetime import date

from django.shortcuts import get_object_or_404, render, redirect
from django.views import View

from dojo.mixins import require_features
from organisations.models import Organisation
from .models import MemberApplication


def _signup_org(org_slug):
    org = get_object_or_404(Organisation, slug=org_slug)
    require_features(org, 'applications')
    return org


def _active_waivers(org):
    if not org.has_feature('documents'):
        return org.waiver_templates.none()
    return org.waiver_templates.filter(is_active=True)


class SignupView(View):
    def get(self, request, org_slug):
        org = _signup_org(org_slug)
        waivers = _active_waivers(org)
        return render(request, 'members/signup.html', {'org': org, 'waivers': waivers})

    def post(self, request, org_slug):
        org = _signup_org(org_slug)
        waivers = _active_waivers(org)
        name = request.POST.get('name', '').strip()
        errors = []

        if not name:
            errors.append('Full name is required.')

        required_waiver = waivers.filter(is_required=True).exists()
        signature_data = request.POST.get('signature_data', '').strip()
        if required_waiver and not signature_data:
            errors.append('Please sign the document before submitting.')

        if errors:
            return render(request, 'members/signup.html', {
                'org': org, 'waivers': waivers, 'errors': errors, 'data': request.POST,
            })

        def posted(name, field_key):
            """A submitted value, or blank when the club has that field switched off."""
            return request.POST.get(name, '').strip() if org.has_field(field_key) else ''

        dob_raw = posted('date_of_birth', 'date_of_birth')
        dob = None
        if dob_raw:
            try:
                dob = date.fromisoformat(dob_raw)
            except ValueError:
                pass

        ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', ''))
        if ',' in ip:
            ip = ip.split(',')[0].strip()

        MemberApplication.objects.create(
            organisation=org,
            name=name,
            date_of_birth=dob,
            email=request.POST.get('email', '').strip(),
            phone=posted('phone', 'phone'),
            address_line1=posted('address_line1', 'address'),
            address_line2=posted('address_line2', 'address'),
            city=posted('city', 'address'),
            county=posted('county', 'address'),
            postcode=posted('postcode', 'address'),
            guardian_name=posted('guardian_name', 'guardians'),
            guardian_email=posted('guardian_email', 'guardians'),
            guardian_phone=posted('guardian_phone', 'guardians'),
            medical_info=posted('medical_info', 'medical_info'),
            notes=request.POST.get('notes', '').strip(),
            signature_data=signature_data,
        )
        return render(request, 'members/signup_done.html', {'org': org})
