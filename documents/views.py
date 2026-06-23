from django.contrib import messages
from django.core.files.base import ContentFile
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.http import FileResponse, Http404

from dojo.mixins import OrgAdminMixin, OrgMixin
from members.models import Member
from .models import Document, SignedWaiver, WaiverTemplate
from .pdf_utils import stamp_signature_on_pdf


class DocumentUploadView(OrgAdminMixin, View):
    def post(self, request, org_slug, member_pk):
        member = get_object_or_404(Member, pk=member_pk, organisation=self.org)
        f = request.FILES.get('file')
        name = request.POST.get('name', '').strip() or (f.name if f else '')
        category = request.POST.get('category', Document.Category.OTHER)
        notes = request.POST.get('notes', '').strip()
        if not f:
            messages.error(request, 'No file selected.')
            return redirect('member_detail', org_slug=org_slug, pk=member_pk)
        Document.objects.create(
            member=member, name=name, category=category,
            file=f, uploaded_by=request.user, notes=notes,
        )
        messages.success(request, f'"{name}" uploaded.')
        return redirect('member_detail', org_slug=org_slug, pk=member_pk)


class DocumentDeleteView(OrgAdminMixin, View):
    def post(self, request, org_slug, member_pk, pk):
        member = get_object_or_404(Member, pk=member_pk, organisation=self.org)
        doc = get_object_or_404(Document, pk=pk, member=member)
        doc.file.delete(save=False)
        doc.delete()
        messages.success(request, 'Document deleted.')
        return redirect('member_detail', org_slug=org_slug, pk=member_pk)


class DocumentDownloadView(OrgAdminMixin, View):
    def get(self, request, org_slug, member_pk, pk):
        member = get_object_or_404(Member, pk=member_pk, organisation=self.org)
        doc = get_object_or_404(Document, pk=pk, member=member)
        try:
            return FileResponse(doc.file.open('rb'), as_attachment=True, filename=doc.name)
        except FileNotFoundError:
            raise Http404


# ── Waiver templates ──────────────────────────────────────────────────────────

class WaiverListView(OrgAdminMixin, View):
    template_name = 'documents/waivers.html'

    def get(self, request, org_slug):
        return render(request, self.template_name, {
            'org': self.org,
            'org_membership': self.org_membership,
            'waivers': self.org.waiver_templates.all(),
        })

    def post(self, request, org_slug):
        f = request.FILES.get('file')
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        is_required = request.POST.get('is_required') == '1'
        if not f or not name:
            messages.error(request, 'Name and file are required.')
            return redirect('waiver_list', org_slug=org_slug)
        WaiverTemplate.objects.create(
            organisation=self.org, name=name, description=description,
            file=f, is_required=is_required,
        )
        messages.success(request, f'"{name}" added.')
        return redirect('waiver_list', org_slug=org_slug)


class WaiverDeleteView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        waiver = get_object_or_404(WaiverTemplate, pk=pk, organisation=self.org)
        waiver.file.delete(save=False)
        waiver.delete()
        messages.success(request, 'Waiver template deleted.')
        return redirect('waiver_list', org_slug=org_slug)


class WaiverDownloadView(OrgAdminMixin, View):
    def get(self, request, org_slug, pk):
        waiver = get_object_or_404(WaiverTemplate, pk=pk, organisation=self.org)
        try:
            return FileResponse(waiver.file.open('rb'), as_attachment=True, filename=waiver.name + '.pdf')
        except FileNotFoundError:
            raise Http404


# ── Signed waivers ────────────────────────────────────────────────────────────

class SignedWaiverDownloadView(OrgAdminMixin, View):
    def get(self, request, org_slug, pk):
        sw = get_object_or_404(SignedWaiver, pk=pk, template__organisation=self.org)
        try:
            return FileResponse(sw.signed_pdf.open('rb'), as_attachment=True,
                                filename=f"{sw.signer_name} — {sw.template.name}.pdf")
        except FileNotFoundError:
            raise Http404


class SignedWaiverOfflineView(OrgAdminMixin, View):
    """Admin uploads a scanned paper waiver for an existing member."""
    def post(self, request, org_slug, member_pk):
        member = get_object_or_404(Member, pk=member_pk, organisation=self.org)
        template_pk = request.POST.get('template_id')
        f = request.FILES.get('file')
        if not template_pk or not f:
            messages.error(request, 'Template and file are required.')
            return redirect('member_detail', org_slug=org_slug, pk=member_pk)
        template = get_object_or_404(WaiverTemplate, pk=template_pk, organisation=self.org)
        SignedWaiver.objects.create(
            member=member, template=template,
            signed_pdf=f, signer_name=member.name,
            signed_at=timezone.now(), offline=True,
        )
        messages.success(request, 'Offline waiver recorded.')
        return redirect('member_detail', org_slug=org_slug, pk=member_pk)


class SignedWaiverDeleteView(OrgAdminMixin, View):
    def post(self, request, org_slug, member_pk, pk):
        member = get_object_or_404(Member, pk=member_pk, organisation=self.org)
        sw = get_object_or_404(SignedWaiver, pk=pk, member=member)
        sw.signed_pdf.delete(save=False)
        sw.delete()
        messages.success(request, 'Signed waiver removed.')
        return redirect('member_detail', org_slug=org_slug, pk=member_pk)
