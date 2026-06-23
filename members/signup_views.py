from datetime import date

from django.shortcuts import get_object_or_404, render, redirect
from django.views import View

from organisations.models import Organisation
from .models import MemberApplication


class SignupView(View):
    def get(self, request, org_slug):
        org = get_object_or_404(Organisation, slug=org_slug)
        waivers = org.waiver_templates.filter(is_active=True)
        return render(request, 'members/signup.html', {'org': org, 'waivers': waivers})

    def post(self, request, org_slug):
        org = get_object_or_404(Organisation, slug=org_slug)
        waivers = org.waiver_templates.filter(is_active=True)
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

        dob_raw = request.POST.get('date_of_birth', '').strip()
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
            phone=request.POST.get('phone', '').strip(),
            address_line1=request.POST.get('address_line1', '').strip(),
            address_line2=request.POST.get('address_line2', '').strip(),
            city=request.POST.get('city', '').strip(),
            county=request.POST.get('county', '').strip(),
            postcode=request.POST.get('postcode', '').strip(),
            guardian_name=request.POST.get('guardian_name', '').strip(),
            guardian_email=request.POST.get('guardian_email', '').strip(),
            guardian_phone=request.POST.get('guardian_phone', '').strip(),
            medical_info=request.POST.get('medical_info', '').strip(),
            notes=request.POST.get('notes', '').strip(),
            signature_data=signature_data,
        )
        return render(request, 'members/signup_done.html', {'org': org})
