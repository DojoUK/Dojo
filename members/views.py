from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from dojo.mixins import OrgAdminMixin
from .forms import GuardianFormSet, MemberForm, build_custom_field_widgets, extract_custom_field_values
from .models import Member


class MemberListView(OrgAdminMixin, ListView):
    template_name = 'members/list.html'
    context_object_name = 'members'
    paginate_by = 50

    def get_queryset(self):
        qs = Member.objects.filter(organisation=self.org).prefetch_related('guardians').order_by('name')
        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(name__icontains=q) | qs.filter(email__icontains=q) | qs.filter(phone__icontains=q)
        show = self.request.GET.get('show', 'active')
        if show == 'inactive':
            qs = qs.filter(is_active=False)
        elif show == 'all':
            pass
        else:
            qs = qs.filter(is_active=True)
        from datetime import date, timedelta
        licence = self.request.GET.get('licence', '')
        today = date.today()
        if licence == 'expired':
            qs = qs.filter(licence_expiry__lt=today)
        elif licence == 'expiring':
            qs = qs.filter(licence_expiry__lte=today + timedelta(days=30), licence_expiry__gte=today)
        waiver = self.request.GET.get('waiver', '')
        if waiver == 'unsigned':
            from documents.models import SignedWaiver, WaiverTemplate
            required = WaiverTemplate.objects.filter(
                organisation=self.org, is_active=True, is_required=True
            )
            signed_ids = SignedWaiver.objects.filter(
                template__in=required
            ).values_list('member_id', flat=True)
            qs = qs.exclude(pk__in=signed_ids)
        return qs

    def get_template_names(self):
        if self.request.htmx:
            return ['members/partials/member_rows.html']
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['q'] = self.request.GET.get('q', '')
        context['show'] = self.request.GET.get('show', 'active')
        context['bulk_email_members'] = self.request.session.pop('bulk_email_members', None)
        context['bulk_invoice_members'] = self.request.session.pop('bulk_invoice_members', None)
        if context['bulk_email_members']:
            context['bulk_email_members'] = Member.objects.filter(pk__in=context['bulk_email_members'])
        if context['bulk_invoice_members']:
            context['bulk_invoice_members'] = Member.objects.filter(pk__in=context['bulk_invoice_members'])
        return context


class MemberBulkActionView(OrgAdminMixin, View):
    def post(self, request, org_slug):
        action = request.POST.get('bulk_action')
        member_ids = [int(x) for x in request.POST.getlist('member_ids') if x.isdigit()]
        members = Member.objects.filter(pk__in=member_ids, organisation=self.org)

        if not members.exists():
            messages.warning(request, 'No members selected.')
            return redirect('member_list', org_slug=org_slug)

        if action == 'archive':
            count = members.filter(is_active=True).update(is_active=False)
            messages.success(request, f'{count} member{"s" if count != 1 else ""} archived.')
            return redirect('member_list', org_slug=org_slug)

        if action == 'email':
            request.session['bulk_email_members'] = list(members.values_list('pk', flat=True))
            return redirect('member_list', org_slug=org_slug)

        if action == 'email_send':
            from django.conf import settings
            from django.core.mail import EmailMultiAlternatives
            subject = request.POST.get('subject', '').strip()
            body = request.POST.get('body', '').strip()
            sent = 0
            for member in members:
                recipient = member.email
                if not recipient:
                    g = member.guardians.filter(email__gt='').first()
                    recipient = g.email if g else None
                if recipient:
                    try:
                        EmailMultiAlternatives(
                            subject=subject, body=body,
                            from_email=settings.DEFAULT_FROM_EMAIL, to=[recipient],
                        ).send()
                        sent += 1
                    except Exception:
                        pass
            messages.success(request, f'Email sent to {sent} member{"s" if sent != 1 else ""}.')
            return redirect('member_list', org_slug=org_slug)

        if action == 'invoice':
            request.session['bulk_invoice_members'] = list(members.values_list('pk', flat=True))
            return redirect('member_list', org_slug=org_slug)

        if action == 'invoice_create':
            from billing.models import Invoice
            from datetime import date
            period = request.POST.get('period', '').strip()
            amount_raw = request.POST.get('amount', '').strip()
            due_date_raw = request.POST.get('due_date', '').strip()
            send_emails = request.POST.get('send_emails') == '1'
            try:
                due_date = date.fromisoformat(due_date_raw)
            except ValueError:
                messages.error(request, 'Invalid due date.')
                return redirect('member_list', org_slug=org_slug)
            created = []
            for member in members:
                amount = amount_raw or (str(member.monthly_fee) if member.monthly_fee else None)
                if not amount:
                    continue
                inv = Invoice.objects.create(
                    organisation=self.org, member=member,
                    amount=amount, period=period, due_date=due_date,
                )
                created.append(inv)
            if send_emails and created:
                from billing.emails import send_invoice_email
                for inv in created:
                    try:
                        send_invoice_email(inv, request=request)
                    except Exception:
                        pass
            messages.success(request, f'{len(created)} invoice{"s" if len(created) != 1 else ""} created.')
            return redirect('member_list', org_slug=org_slug)

        messages.error(request, 'Unknown action.')
        return redirect('member_list', org_slug=org_slug)


class MemberCreateView(OrgAdminMixin, CreateView):
    template_name = 'members/form.html'
    form_class = MemberForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['org'] = self.org
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Add member'
        if self.request.POST:
            context['guardian_formset'] = GuardianFormSet(self.request.POST)
        else:
            context['guardian_formset'] = GuardianFormSet()
        context['custom_fields'] = build_custom_field_widgets(self.org, self.request.POST or None)
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        guardian_formset = context['guardian_formset']
        if not guardian_formset.is_valid():
            return self.form_invalid(form)
        member = form.save(commit=False)
        member.organisation = self.org
        member.custom_field_values = extract_custom_field_values(self.org, self.request.POST)
        member.save()
        guardian_formset.instance = member
        guardian_formset.save()
        self._auto_assign_progression(member)
        messages.success(self.request, f'{member.name} added successfully.')
        return redirect('member_detail', org_slug=self.org.slug, pk=member.pk)

    def _auto_assign_progression(self, member):
        from django.utils import timezone
        from progression.models import MemberProgression, ProgressionSystem
        today = timezone.localdate()
        for system in ProgressionSystem.objects.filter(organisation=self.org, assign_to_new_members=True):
            default_stage = system.stages.filter(is_default=True).first()
            if default_stage:
                MemberProgression.objects.create(
                    member=member,
                    stage=default_stage,
                    achieved_date=today,
                    notes='Auto-assigned on registration.',
                )


class MemberDetailView(OrgAdminMixin, DetailView):
    template_name = 'members/detail.html'
    context_object_name = 'member'

    def get_object(self):
        return get_object_or_404(Member, pk=self.kwargs['pk'], organisation=self.org)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['guardians'] = self.object.guardians.all()
        context['invoices'] = self.object.invoices.order_by('-created_at')[:5]
        from classes.models import ClassMember
        context['enrolments'] = (
            ClassMember.objects.filter(member=self.object)
            .select_related('assigned_class')
            .order_by('assigned_class__name')
        )
        from progression.models import MemberProgression, ProgressionStage, ProgressionSystem
        context['progressions'] = MemberProgression.objects.filter(
            member=self.object
        ).select_related('stage__system').order_by('-achieved_date')
        context['stages'] = ProgressionStage.objects.filter(
            system__organisation=self.org
        ).select_related('system')
        context['systems'] = ProgressionSystem.objects.filter(
            organisation=self.org
        ).prefetch_related('stages')
        context['current_grade'] = context['progressions'].first()
        from .models import MemberNote
        context['notes'] = MemberNote.objects.filter(member=self.object).select_related('author')
        context['custom_fields'] = build_custom_field_widgets(
            self.org, initial_values=self.object.custom_field_values
        )
        from datetime import date, timedelta
        today = date.today()
        context['today'] = today
        context['thirty_days'] = today + timedelta(days=30)
        from documents.models import SignedWaiver, WaiverTemplate
        context['signed_waivers'] = SignedWaiver.objects.filter(
            member=self.object
        ).select_related('template').order_by('-signed_at')
        context['waiver_templates'] = WaiverTemplate.objects.filter(
            organisation=self.org, is_active=True
        )
        signed_template_ids = set(context['signed_waivers'].values_list('template_id', flat=True))
        context['missing_waivers'] = context['waiver_templates'].filter(
            is_required=True
        ).exclude(pk__in=signed_template_ids)
        from billing.models import BillingPolicy, PolicyDiscount, MemberDiscount
        context['member_discounts'] = MemberDiscount.objects.filter(
            member=self.object, is_active=True
        ).select_related('discount__policy')
        context['org_policies'] = BillingPolicy.objects.filter(
            organisation=self.org, is_active=True
        ).prefetch_related('discounts')
        context['available_discounts'] = PolicyDiscount.objects.filter(
            policy__organisation=self.org
        ).exclude(
            member_discounts__member=self.object, member_discounts__is_active=True
        ).select_related('policy')
        return context


class MemberUpdateView(OrgAdminMixin, UpdateView):
    template_name = 'members/form.html'
    form_class = MemberForm

    def get_object(self):
        return get_object_or_404(Member, pk=self.kwargs['pk'], organisation=self.org)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['org'] = self.org
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'Edit {self.object.name}'
        context['member'] = self.object
        if self.request.POST:
            context['guardian_formset'] = GuardianFormSet(self.request.POST, instance=self.object)
        else:
            context['guardian_formset'] = GuardianFormSet(instance=self.object)
        context['custom_fields'] = build_custom_field_widgets(
            self.org,
            self.request.POST or None,
            initial_values=self.object.custom_field_values,
        )
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        guardian_formset = context['guardian_formset']
        if not guardian_formset.is_valid():
            return self.form_invalid(form)
        member = form.save(commit=False)
        member.custom_field_values = extract_custom_field_values(self.org, self.request.POST)
        member.save()
        guardian_formset.instance = member
        guardian_formset.save()
        messages.success(self.request, f'{member.name} updated successfully.')
        return redirect('member_detail', org_slug=self.org.slug, pk=member.pk)


class MemberArchiveView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        from django.utils import timezone
        member = get_object_or_404(Member, pk=pk, organisation=self.org)
        member.is_active = not member.is_active
        if not member.is_active:
            member.archived_at = timezone.now()
        else:
            member.archived_at = None
        member.save(update_fields=['is_active', 'archived_at'])
        status = 'reactivated' if member.is_active else 'archived'
        messages.success(request, f'{member.name} {status}.')
        return redirect('member_detail', org_slug=self.org.slug, pk=member.pk)


class RecordPromotionView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        member = get_object_or_404(Member, pk=pk, organisation=self.org)
        from progression.models import MemberProgression, ProgressionStage
        stage_pk = request.POST.get('stage_id')
        achieved_date = request.POST.get('achieved_date')
        notes = request.POST.get('notes', '').strip()
        stage = get_object_or_404(ProgressionStage, pk=stage_pk, system__organisation=self.org)
        MemberProgression.objects.create(
            member=member,
            stage=stage,
            achieved_date=achieved_date,
            notes=notes,
        )
        messages.success(request, f'{member.name} promoted to {stage.name}.')
        return redirect('member_detail', org_slug=self.org.slug, pk=member.pk)


class DeleteProgressionView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk, prog_pk):
        member = get_object_or_404(Member, pk=pk, organisation=self.org)
        from progression.models import MemberProgression
        prog = get_object_or_404(MemberProgression, pk=prog_pk, member=member)
        prog.delete()
        messages.success(request, 'Progression record removed.')
        return redirect('member_detail', org_slug=self.org.slug, pk=member.pk)


class ApplicationListView(OrgAdminMixin, View):
    def get(self, request, org_slug):
        from django.shortcuts import render
        from .models import MemberApplication
        applications = MemberApplication.objects.filter(organisation=self.org)
        status = request.GET.get('status', 'pending')
        if status in ('pending', 'approved', 'rejected'):
            applications = applications.filter(status=status)
        return render(request, 'members/applications.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'applications': applications,
            'status_filter': status,
        })


class ApproveApplicationView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        from .models import MemberApplication
        app = get_object_or_404(MemberApplication, pk=pk, organisation=self.org)
        member = Member.objects.create(
            organisation=self.org,
            name=app.name,
            date_of_birth=app.date_of_birth,
            email=app.email,
            phone=app.phone,
            medical_info=app.medical_info,
        )
        if app.guardian_name:
            from .models import Guardian
            Guardian.objects.create(
                member=member,
                name=app.guardian_name,
                email=app.guardian_email,
                phone=app.guardian_phone,
                relationship='Guardian',
            )
        app.status = MemberApplication.Status.APPROVED
        app.save(update_fields=['status'])

        # Generate signed waivers from application signature
        if app.signature_data:
            from documents.models import WaiverTemplate, SignedWaiver
            from documents.pdf_utils import stamp_signature_on_pdf
            from django.core.files.base import ContentFile
            for template in self.org.waiver_templates.filter(is_active=True):
                try:
                    signed_pdf = stamp_signature_on_pdf(
                        template.file, app.signature_data, app.name, app.submitted_at
                    )
                    sw = SignedWaiver(
                        member=member, application=app, template=template,
                        signer_name=app.name, signed_at=app.submitted_at,
                    )
                    sw.signed_pdf.save(
                        f"{app.name}_{template.name}.pdf".replace(' ', '_'),
                        ContentFile(signed_pdf.read()), save=True
                    )
                except Exception:
                    pass  # Don't block approval if PDF stamping fails

        from .emails import send_welcome_email
        send_welcome_email(member)
        messages.success(request, f'{member.name} approved and added as a member.')
        return redirect('member_detail', org_slug=self.org.slug, pk=member.pk)


class RejectApplicationView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        from .models import MemberApplication
        app = get_object_or_404(MemberApplication, pk=pk, organisation=self.org)
        app.status = MemberApplication.Status.REJECTED
        app.save(update_fields=['status'])
        messages.success(request, f"Application from {app.name} rejected.")
        return redirect('application_list', org_slug=self.org.slug)


class AddMemberNoteView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        member = get_object_or_404(Member, pk=pk, organisation=self.org)
        body = request.POST.get('body', '').strip()
        if body:
            from .models import MemberNote
            MemberNote.objects.create(member=member, author=request.user, body=body)
        return redirect('member_detail', org_slug=self.org.slug, pk=pk)


class DeleteMemberNoteView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk, note_pk):
        member = get_object_or_404(Member, pk=pk, organisation=self.org)
        from .models import MemberNote
        note = get_object_or_404(MemberNote, pk=note_pk, member=member)
        note.delete()
        return redirect('member_detail', org_slug=self.org.slug, pk=pk)


class MemberExportView(OrgAdminMixin, View):
    def get(self, request, org_slug):
        import csv
        from django.http import HttpResponse
        members = (
            Member.objects.filter(organisation=self.org)
            .prefetch_related('guardians', 'enrolments__assigned_class')
            .order_by('name')
        )
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{self.org.slug}-members.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'Name', 'Date of birth', 'Email', 'Phone',
            'Joined date', 'Active', 'Monthly fee',
            'Guardian name', 'Guardian email', 'Guardian phone',
            'Classes',
        ])
        for m in members:
            guardian = m.guardians.first()
            classes = ', '.join(e.assigned_class.name for e in m.enrolments.all())
            writer.writerow([
                m.name,
                m.date_of_birth.isoformat() if m.date_of_birth else '',
                m.email,
                m.phone,
                m.joined_date.isoformat() if m.joined_date else '',
                'Yes' if m.is_active else 'No',
                str(m.monthly_fee) if m.monthly_fee else '',
                guardian.name if guardian else '',
                guardian.email if guardian else '',
                guardian.phone if guardian else '',
                classes,
            ])
        return response


class MemberImportView(OrgAdminMixin, View):
    template_name = 'members/import.html'

    EXPECTED_HEADERS = ['name', 'date_of_birth', 'email', 'phone', 'joined_date', 'notes']

    def get(self, request, org_slug):
        return self._render(request)

    def _render(self, request, preview=None, errors=None, raw_csv=None):
        from django.shortcuts import render
        return render(request, self.template_name, {
            'org': self.org,
            'org_membership': self.org_membership,
            'preview': preview,
            'errors': errors or [],
            'raw_csv': raw_csv or '',
        })

    def post(self, request, org_slug):
        import csv, io
        action = request.POST.get('action', 'preview')
        raw_csv = ''

        if 'csv_file' in request.FILES:
            raw_csv = request.FILES['csv_file'].read().decode('utf-8-sig').strip()
        elif request.POST.get('raw_csv'):
            raw_csv = request.POST.get('raw_csv', '').strip()

        if not raw_csv:
            messages.error(request, 'No data provided.')
            return self._render(request)

        reader = csv.DictReader(io.StringIO(raw_csv))
        if not reader.fieldnames:
            messages.error(request, 'Could not parse CSV — check the format.')
            return self._render(request, raw_csv=raw_csv)

        # Normalise headers (lowercase, strip whitespace)
        reader.fieldnames = [h.strip().lower().replace(' ', '_') for h in reader.fieldnames]
        if 'name' not in reader.fieldnames:
            messages.error(request, 'CSV must have a "name" column.')
            return self._render(request, raw_csv=raw_csv)

        rows, errors = [], []
        for i, row in enumerate(reader, start=2):
            name = (row.get('name') or '').strip()
            if not name:
                errors.append(f'Row {i}: missing name, skipped.')
                continue

            dob_raw = (row.get('date_of_birth') or row.get('dob') or '').strip()
            dob = None
            if dob_raw:
                from datetime import date as ddate
                for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
                    try:
                        dob = ddate.fromisoformat(dob_raw) if fmt == '%Y-%m-%d' else ddate.strptime(dob_raw, fmt)
                        break
                    except ValueError:
                        continue
                if not dob:
                    errors.append(f'Row {i} ({name}): unrecognised date format "{dob_raw}" — date_of_birth left blank.')

            joined_raw = (row.get('joined_date') or row.get('joined') or '').strip()
            joined = None
            if joined_raw:
                for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
                    try:
                        joined = ddate.fromisoformat(joined_raw) if fmt == '%Y-%m-%d' else ddate.strptime(joined_raw, fmt)
                        break
                    except ValueError:
                        continue

            rows.append({
                'name': name,
                'email': (row.get('email') or '').strip(),
                'phone': (row.get('phone') or '').strip(),
                'date_of_birth': dob,
                'joined_date': joined,
            })

        if action == 'preview':
            return self._render(request, preview=rows, errors=errors, raw_csv=raw_csv)

        # action == 'import'
        created = 0
        for r in rows:
            Member.objects.create(
                organisation=self.org,
                name=r['name'],
                email=r['email'],
                phone=r['phone'],
                date_of_birth=r['date_of_birth'],
                joined_date=r['joined_date'],
            )
            created += 1

        messages.success(request, f'{created} member{"s" if created != 1 else ""} imported.')
        if errors:
            for e in errors:
                messages.warning(request, e)
        return redirect('member_list', org_slug=self.org.slug)


class SendWelcomeEmailView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        member = get_object_or_404(Member, pk=pk, organisation=self.org)
        from .emails import send_welcome_email
        try:
            ok, result = send_welcome_email(member)
            if ok:
                messages.success(request, f'Welcome email sent to {result}.')
            else:
                messages.error(request, f'Could not send email: {result}')
        except Exception as e:
            messages.error(request, f'Email failed: {e}')
        return redirect('member_detail', org_slug=self.org.slug, pk=member.pk)


class MemberCustomFieldsView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        member = get_object_or_404(Member, pk=pk, organisation=self.org)
        member.custom_field_values = extract_custom_field_values(self.org, request.POST)
        member.save(update_fields=['custom_field_values'])
        messages.success(request, 'Custom fields updated.')
        return redirect('member_detail', org_slug=self.org.slug, pk=pk)


class FormerMembersView(OrgAdminMixin, View):
    template_name = 'members/former_members.html'

    def get(self, request, org_slug):
        from datetime import timedelta
        from django.utils import timezone
        cutoff = timezone.now() - timedelta(days=3 * 365)
        former = (
            Member.objects.filter(organisation=self.org, is_active=False)
            .order_by('-archived_at', 'name')
        )
        return render(request, self.template_name, {
            'org': self.org,
            'org_membership': self.org_membership,
            'former_members': former,
            'retention_cutoff': cutoff,
        })


class MemberRetentionNotesView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        member = get_object_or_404(Member, pk=pk, organisation=self.org)
        member.retention_notes = request.POST.get('retention_notes', '').strip()
        member.save(update_fields=['retention_notes'])
        messages.success(request, 'Retention notes saved.')
        return redirect('former_members', org_slug=self.org.slug)
