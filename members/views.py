from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from dojo.mixins import OrgAdminMixin
from .forms import GuardianFormSet, MemberForm, build_custom_field_widgets, extract_custom_field_values
from .models import FamilyGroupMember, Member
from .navigation import redirect_to_member
from mailer import connection_for


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
        elif show == 'leaving':
            from .models import MemberLeavingRequest
            qs = qs.filter(leaving_requests__status=MemberLeavingRequest.Status.PENDING).distinct()
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
        from .models import MemberLeavingRequest
        context['leaving_count'] = MemberLeavingRequest.objects.filter(
            member__organisation=self.org, status=MemberLeavingRequest.Status.PENDING
        ).count()
        context['bulk_email_members'] = self.request.session.pop('bulk_email_members', None)
        context['bulk_invoice_members'] = self.request.session.pop('bulk_invoice_members', None)
        if context['bulk_email_members']:
            context['bulk_email_members'] = Member.objects.filter(pk__in=context['bulk_email_members'])
        if context['bulk_invoice_members']:
            context['bulk_invoice_members'] = Member.objects.filter(pk__in=context['bulk_invoice_members'])
        return context


class NotSeenLatelyView(OrgAdminMixin, View):
    """Members the club may be about to lose: not seen lately, never come, or coming less often."""

    def get(self, request, org_slug):
        from . import not_seen
        try:
            days = int(request.GET.get('days', not_seen.DEFAULT_DAYS))
        except ValueError:
            days = not_seen.DEFAULT_DAYS
        if days not in not_seen.DAY_CHOICES:
            days = not_seen.DEFAULT_DAYS
        found = not_seen.report(self.org, days)
        if request.GET.get('format') == 'csv':
            return self._csv(found)
        sections = [
            ('not_seen', f'Not seen in {days} days or more', 'They used to come, but haven\'t been marked present since.',
             found['not_seen']),
            ('slowing', 'Coming less often', 'Still coming, but in the last 4 weeks about half as often as usual or less.',
             found['slowing']),
            ('never', 'Never come', f'No attendance recorded, and they joined {days} days ago or more (or no join date).',
             found['never']),
        ]
        return render(request, 'members/not_seen.html', {
            'org': self.org, 'org_membership': self.org_membership, 'days': days,
            'day_choices': not_seen.DAY_CHOICES, 'sections': sections,
            'total': sum(len(rows) for *_, rows in sections),
        })

    def _csv(self, found):
        import csv
        from django.http import HttpResponse
        from .emails import _recipient
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="not-seen-lately.csv"'
        writer = csv.writer(response)
        writer.writerow(['Group', 'Name', 'Last seen', 'Days since', 'Last 4 weeks', 'Usual 4 weeks', 'Classes',
                         'Email', 'Phone', 'Leaving requested', 'Overdue invoices'])
        labels = {'not_seen': 'Not seen', 'slowing': 'Coming less often', 'never': 'Never come'}
        for key, rows in found.items():
            for row in rows:
                m = row['member']
                writer.writerow([labels[key], m.name, row['last_seen'] or '', row['days_since'] if row['days_since'] is not None else '',
                                 row['recent'], row['usual'], ', '.join(m.class_names), _recipient(m) or '', m.phone,
                                 'Yes' if m.leaving else '', m.overdue or ''])
        return response


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
                            connection=connection_for(self.org),
                        ).send()
                        sent += 1
                    except Exception:
                        pass
            messages.success(request, f'Email sent to {sent} member{"s" if sent != 1 else ""}.')
            return redirect('member_list', org_slug=org_slug)

        if action in ('invoice', 'invoice_create') and not self.org.has_feature('billing'):
            raise Http404

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
                from billing.credit import apply_member_credit
                apply_member_credit(inv)
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
        if not self.org.has_field('guardians'):
            context['guardian_formset'] = None
        elif self.request.POST:
            context['guardian_formset'] = GuardianFormSet(self.request.POST)
        else:
            context['guardian_formset'] = GuardianFormSet()
        context['custom_fields'] = build_custom_field_widgets(self.org, self.request.POST or None)
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        guardian_formset = context['guardian_formset']
        if guardian_formset is not None and not guardian_formset.is_valid():
            return self.form_invalid(form)
        member = form.save(commit=False)
        member.organisation = self.org
        member.custom_field_values = extract_custom_field_values(self.org, self.request.POST)
        member.save()
        if guardian_formset is not None:
            guardian_formset.instance = member
            guardian_formset.save()
        self._auto_assign_progression(member)
        messages.success(self.request, f'{member.name} added successfully.')
        return redirect('member_detail', org_slug=self.org.slug, pk=member.pk)

    def _auto_assign_progression(self, member):
        from django.utils import timezone
        from progression.models import MemberProgression, ProgressionSystem
        if not self.org.has_feature('grades'):
            return
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
        from billing.credit import member_credit
        context['overpaid_credit'] = member_credit(self.object)
        from classes.models import Attendance
        attended = Attendance.objects.filter(member=self.object, present=True)
        context['sessions_attended'] = attended.count()
        context['recent_attended'] = list(
            attended.select_related('session__assigned_class').order_by('-session__date')[:5]
        )
        context['last_attended'] = context['recent_attended'][0].session.date if context['recent_attended'] else None
        from classes.models import ClassMember
        context['enrolments'] = (
            ClassMember.objects.filter(member=self.object)
            .select_related('assigned_class')
            .order_by('assigned_class__name')
        )
        context['session_counts'] = self.object.session_counts()
        from progression.models import MemberProgression, ProgressionStage, ProgressionSystem
        context['progressions'] = MemberProgression.objects.filter(
            member=self.object
        ).select_related('stage__system', 'grading_candidate__session__grading').order_by('-achieved_date')
        context['stages'] = ProgressionStage.objects.filter(
            system__organisation=self.org
        ).select_related('system')
        context['systems'] = ProgressionSystem.objects.filter(
            organisation=self.org
        ).prefetch_related('stages')
        context['current_grade'] = context['progressions'].first()
        # Their progression from other clubs and grades they added themselves: theirs, so read only here.
        from network.progression import elsewhere
        context['progression_elsewhere'] = elsewhere(self.object) if self.org.has_feature('grades') else []

        from progression.models import MemberSyllabusProgress
        current_by_system = {}
        for prog in context['progressions']:
            current_by_system.setdefault(prog.stage.system_id, prog)
        syllabus_cards = []
        for prog in current_by_system.values():
            section = prog.stage.syllabus_section
            if not section:
                continue
            items = list(section.items.all())
            done_ids = set(
                MemberSyllabusProgress.objects.filter(
                    member=self.object, item__in=items, completed=True
                ).values_list('item_id', flat=True)
            )
            from progression.models import split_syllabus_columns
            groups = [
                {'subsection': g['subsection'], 'items': [{'item': i, 'done': i.pk in done_ids} for i in g['items']]}
                for g in section.grouped_items()
            ]
            syllabus_cards.append({
                'stage': prog.stage,
                'section': section,
                'groups': groups,
                'columns': split_syllabus_columns(groups),
            })
        context['syllabus_cards'] = syllabus_cards

        from .models import MemberLeavingRequest
        context['leaving_request'] = self.object.leaving_requests.filter(
            status=MemberLeavingRequest.Status.PENDING
        ).first()
        # DojoUK's syllabus (technique sign-offs), shown only when a club switches the
        # Syllabus feature on. This fork's own syllabus checklist is `syllabus_cards` above.
        context['technique_syllabus_cards'] = []
        if self.org.has_feature('syllabus'):
            from progression.syllabus import current_syllabuses
            context['technique_syllabus_cards'] = current_syllabuses(self.object)
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
        from . import family
        context['family_group'] = family.family_group(self.object)
        context['family_people'] = family.siblings(self.object) if context['family_group'] else []
        in_family = {m.pk for m in family.family_members(self.object, active_only=False)}
        in_any_group = set(FamilyGroupMember.objects.filter(family_group__organisation=self.org)
                           .values_list('member_id', flat=True))
        context['family_candidates'] = Member.objects.filter(organisation=self.org, is_active=True) \
            .exclude(pk__in=in_family | in_any_group).order_by('name')
        # Auto-apply discounts on the plans they're billed on: applied unless exempted.
        from .models import current_plans
        exempt = {md.discount_id: md for md in MemberDiscount.objects.filter(member=self.object, is_active=False)}
        has_active = {md.discount_id for md in context['member_discounts']}
        context['auto_discounts'] = [
            {'discount': d, 'exemption': exempt.get(d.pk)}
            for d in PolicyDiscount.objects.filter(policy__in=current_plans(self.object), auto_apply=True)
            .select_related('policy') if d.pk not in has_active
        ]
        context['available_discounts'] = PolicyDiscount.objects.filter(
            policy__organisation=self.org, auto_apply=False
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
            context['guardian_formset'] = (
                GuardianFormSet(self.request.POST, instance=self.object)
                if self.org.has_field('guardians') else None
            )
        else:
            context['guardian_formset'] = (
                GuardianFormSet(instance=self.object) if self.org.has_field('guardians') else None
            )
        context['custom_fields'] = build_custom_field_widgets(
            self.org,
            self.request.POST or None,
            initial_values=self.object.custom_field_values,
        )
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        guardian_formset = context['guardian_formset']
        if guardian_formset is not None and not guardian_formset.is_valid():
            return self.form_invalid(form)
        member = form.save(commit=False)
        member.custom_field_values = extract_custom_field_values(self.org, self.request.POST)
        member.save()
        if guardian_formset is not None:
            guardian_formset.instance = member
            guardian_formset.save()
        messages.success(self.request, f'{member.name} updated successfully.')
        return redirect('member_detail', org_slug=self.org.slug, pk=member.pk)


class MemberBillingPolicySetView(OrgAdminMixin, View):
    required_feature = 'billing'

    def post(self, request, org_slug, pk):
        from billing.models import BillingPolicy
        member = get_object_or_404(Member, pk=pk, organisation=self.org)
        from .family import assign_plan
        policy_id = request.POST.get('billing_policy')
        policy = get_object_or_404(BillingPolicy, pk=policy_id, organisation=self.org) if policy_id else None
        from .family import FamilyPlanFull
        try:
            changed = assign_plan(member, policy)
        except FamilyPlanFull as e:
            messages.error(request, str(e))
            return redirect_to_member(self.org.slug, member.pk, 'billing')
        if len(changed) > 1:
            messages.success(request, f'{policy.name} is a family plan, so it is now on all {len(changed)} family '
                                      f'members: {", ".join(m.name for m in changed)}.')
        else:
            messages.success(request, f'Billing policy updated for {member.name}.')
        return redirect_to_member(self.org.slug, member.pk, 'billing')


class MemberFamilyView(OrgAdminMixin, View):
    """Link members into a family (they then share one member portal), unlink them, or edit the family."""

    def post(self, request, org_slug, pk):
        from decimal import Decimal, InvalidOperation

        from . import family
        member = get_object_or_404(Member, pk=pk, organisation=self.org)
        action = request.POST.get('action', '')
        back = redirect_to_member(self.org.slug, member.pk, 'billing')
        if action == 'add':
            other = Member.objects.filter(pk=request.POST.get('member_id') or 0, organisation=self.org).first()
            if other is None or other.pk == member.pk:
                messages.error(request, 'Choose someone to add to the family.')
                return back
            existing = family.family_group(other)
            if existing is not None and existing != family.family_group(member):
                messages.error(request, f'{other.name} is already in the {existing.name} group. '
                                        f'Remove them from it first.')
                return back
            group = family.link(member, other)
            messages.success(request, f'{other.name} is now in the {group.name} group with {member.name}. '
                                      f'They share one member portal.')
        elif action == 'remove':
            other = Member.objects.filter(pk=request.POST.get('member_id') or 0, organisation=self.org).first()
            group = family.family_group(member)
            if other is None or group is None or not group.memberships.filter(member=other).exists():
                messages.error(request, 'That person isn\'t in this family.')
                return back
            family.unlink(other)
            messages.success(request, f'{other.name} has been taken out of the family.')
        elif action == 'update':
            group = family.family_group(member)
            if group is None:
                return back
            name = request.POST.get('name', '').strip()
            try:
                discount = Decimal(request.POST.get('discount_percentage', '0') or '0')
            except InvalidOperation:
                discount = Decimal('-1')
            if not name or not Decimal('0') <= discount <= Decimal('100'):
                messages.error(request, 'Give the family a name and a discount between 0 and 100%.')
                return back
            group.name = name
            group.discount_percentage = discount.quantize(Decimal('0.01'))
            group.save(update_fields=['name', 'discount_percentage'])
            messages.success(request, 'Family updated.')
        return back


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


class ResolveLeavingRequestView(OrgAdminMixin, View):
    """Staff action on a member's self-reported "stop training" request from the portal — either archive them or dismiss the request."""
    def post(self, request, org_slug, pk, request_pk):
        from django.utils import timezone
        from .models import MemberLeavingRequest
        member = get_object_or_404(Member, pk=pk, organisation=self.org)
        leaving_request = get_object_or_404(MemberLeavingRequest, pk=request_pk, member=member)
        resolution = request.POST.get('resolution')

        if resolution == 'archive':
            member.is_active = False
            member.archived_at = timezone.now()
            member.save(update_fields=['is_active', 'archived_at'])
            leaving_request.status = MemberLeavingRequest.Status.ACTIONED
            messages.success(request, f'{member.name} archived.')
        else:
            leaving_request.status = MemberLeavingRequest.Status.DISMISSED
            messages.success(request, 'Leaving request dismissed.')

        leaving_request.resolved_at = timezone.now()
        leaving_request.resolved_by = request.user
        leaving_request.save(update_fields=['status', 'resolved_at', 'resolved_by'])
        return redirect('member_detail', org_slug=self.org.slug, pk=member.pk)


class EraseMemberView(OrgAdminMixin, View):
    """Right-to-erasure action: scrubs personal data, keeps the row for financial/attendance FK integrity."""
    def post(self, request, org_slug, pk):
        member = get_object_or_404(Member, pk=pk, organisation=self.org)
        if member.is_active:
            messages.error(request, 'Archive the member before erasing their data.')
            return redirect('member_detail', org_slug=self.org.slug, pk=member.pk)
        if member.anonymised_at:
            messages.info(request, 'This member\'s data has already been erased.')
            return redirect('former_members', org_slug=self.org.slug)
        if member.retention_notes:
            messages.error(request, 'This member has a retention override recorded — clear the retention notes before erasing.')
            return redirect('former_members', org_slug=self.org.slug)
        member.anonymise()
        messages.success(request, 'Member data erased.')
        return redirect('former_members', org_slug=self.org.slug)


class RecordPromotionView(OrgAdminMixin, View):
    required_feature = 'grades'

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
        return redirect_to_member(self.org.slug, member.pk, 'grades')


class ToggleTechniqueView(OrgAdminMixin, View):
    """Sign a syllabus technique off for a member, or undo it."""
    required_feature = 'syllabus'

    def post(self, request, org_slug, pk, technique_pk):
        from progression.models import MemberTechnique, Technique
        member = get_object_or_404(Member, pk=pk, organisation=self.org)
        technique = get_object_or_404(Technique, pk=technique_pk, organisation=self.org)
        signed_off = MemberTechnique.objects.filter(member=member, technique=technique).first()
        if signed_off:
            signed_off.delete()
            signed_off = None
        else:
            signed_off, _ = MemberTechnique.objects.get_or_create(
                member=member, technique=technique, defaults={'signed_off_by': request.user},
            )
        if request.htmx:
            from progression.syllabus import current_syllabuses
            cards = current_syllabuses(member)
            # The technique can be in more than one of the member's syllabuses:
            # the ticked row is swapped in place and the others out of band.
            rows = [
                (card['syllabus'], entry)
                for card in cards for _, entries in card['groups'] for entry in entries
                if entry.technique_id == technique.pk
            ]
            ticked = next((row for row in rows if str(row[0].pk) == request.POST.get('syllabus')), None)
            if ticked is None:  # the page is out of date (grade or syllabus changed): reload it
                response = HttpResponse(status=204)
                response['HX-Refresh'] = 'true'
                return response
            return render(request, 'members/_technique_signoff_response.html', {
                'org': self.org, 'member': member, 'row': ticked,
                'other_rows': [row for row in rows if row is not ticked],
                'cards': cards,  # refreshes each syllabus's "N of M" count
            })
        return redirect_to_member(self.org.slug, member.pk, 'grades')


class DeleteProgressionView(OrgAdminMixin, View):
    required_feature = 'grades'

    def post(self, request, org_slug, pk, prog_pk):
        member = get_object_or_404(Member, pk=pk, organisation=self.org)
        from progression.models import MemberProgression
        prog = get_object_or_404(MemberProgression, pk=prog_pk, member=member)
        candidate = getattr(prog, 'grading_candidate', None)
        if candidate is not None:
            # Deleting it here would leave the grading saying "passed" with no grade behind it.
            messages.error(
                request,
                f'This grade was awarded at {candidate.session.grading.name}. Change the result there instead.',
            )
            return redirect_to_member(self.org.slug, member.pk, 'grades')
        prog.delete()
        messages.success(request, 'Progression record removed.')
        return redirect_to_member(self.org.slug, member.pk, 'grades')


class ToggleMemberSyllabusItemView(OrgAdminMixin, View):
    """Staff ticks/unticks a syllabus checklist item as covered for this member."""
    def post(self, request, org_slug, pk, item_pk):
        from django.utils import timezone
        from progression.models import MemberSyllabusProgress, SyllabusItem

        member = get_object_or_404(Member, pk=pk, organisation=self.org)
        item = get_object_or_404(SyllabusItem, pk=item_pk, section__organisation=self.org)
        progress, _ = MemberSyllabusProgress.objects.get_or_create(member=member, item=item)
        progress.completed = not progress.completed
        progress.completed_at = timezone.now() if progress.completed else None
        progress.completed_by = request.user if progress.completed else None
        progress.save(update_fields=['completed', 'completed_at', 'completed_by'])
        if request.htmx:
            # Swap just this checklist item in place instead of reloading the page.
            from django.shortcuts import render
            return render(request, 'members/_syllabus_item.html', {
                'org': self.org,
                'member': member,
                'entry': {'item': item, 'done': progress.completed},
            })
        return redirect('member_detail', org_slug=self.org.slug, pk=member.pk)


class ApplicationListView(OrgAdminMixin, View):
    required_feature = 'applications'

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
    required_feature = 'applications'

    def post(self, request, org_slug, pk):
        from django.utils import timezone
        from .models import MemberApplication
        app = get_object_or_404(MemberApplication, pk=pk, organisation=self.org)
        member = Member.objects.create(
            organisation=self.org,
            account=app.account,   # applied from a Dojo account: the membership shows up there
            account_role=(('' if not app.account_id else
                           Member.AccountRole.HOLDER if app.for_account_holder else Member.AccountRole.DEPENDANT)),
            name=app.name,
            first_name=app.first_name,
            surname=app.surname,
            preferred_name=app.preferred_name,
            pronouns=app.pronouns,
            date_of_birth=app.date_of_birth,
            email=app.email,
            phone=app.phone,
            medical_info=app.medical_info,
            address_line1=app.address_line1,
            address_line2=app.address_line2,
            city=app.city,
            county=app.county,
            postcode=app.postcode,
            emergency_contact_name=app.emergency_contact_name,
            emergency_contact_phone=app.emergency_contact_phone,
            emergency_contact_2_name=app.emergency_contact_2_name,
            emergency_contact_2_phone=app.emergency_contact_2_phone,
            joined_date=timezone.localdate(),
            # Answers to custom fields asked on the join form, for fields the club still has.
            custom_field_values={key: value for key, value in (app.custom_field_values or {}).items()
                                 if self.org.custom_fields.filter(pk=key).exists()},
        )
        if app.guardian_name:
            from .models import Guardian
            Guardian.objects.create(
                member=member,
                name=app.guardian_name,
                email=app.guardian_email,
                phone=app.guardian_phone,
                relationship='Guardian',
                # A parent applying from their account for their child: the guardian is the account holder.
                account=(app.account if app.account_id and not app.for_account_holder
                         and app.guardian_email.strip().lower() == app.account.email.lower() else None),
            )
        if app.promo_id:
            from billing import promos
            try:
                promos.redeem(member, app.promo)
            except ValueError as e:
                messages.warning(request, f'The promo code they used wasn\'t applied: {e}')
        app.status = MemberApplication.Status.APPROVED
        app.decided_at = timezone.now()
        app.save(update_fields=['status', 'decided_at'])

        # Generate signed waivers from application signature
        if app.signature_data:
            from documents.models import WaiverTemplate, SignedWaiver
            from documents.pdf_utils import stamp_signature_on_pdf
            from django.core.files.base import ContentFile
            templates = self.org.waiver_templates.filter(is_active=True)
            if app.agreed_waivers:   # the documents they ticked; older applications agreed to all
                templates = templates.filter(pk__in=app.agreed_waivers)
            signer = app.signer_name or app.name
            for template in templates:
                try:
                    signed_pdf = stamp_signature_on_pdf(
                        template.file, app.signature_data, signer, app.submitted_at
                    )
                    sw = SignedWaiver(
                        member=member, application=app, template=template,
                        signer_name=signer, signed_at=app.submitted_at, ip_address=app.signer_ip,
                    )
                    sw.signed_pdf.save(
                        f"{app.name}_{template.name}.pdf".replace(' ', '_'),
                        ContentFile(signed_pdf.read()), save=True
                    )
                except Exception:
                    pass  # Don't block approval if PDF stamping fails

        messages.success(request, f'{member.name} approved and added as a member.')
        if self.org.has_feature('member_portal'):
            # The member is already added, so a failed welcome email mustn't turn this into an error page.
            from .emails import send_welcome_email
            try:
                send_welcome_email(member)
            except Exception as e:
                messages.warning(request, f'The welcome email couldn\'t be sent ({e}). Check Settings → Email, then '
                                          f'use "Send welcome email" on {member.name}\'s page.')
        return redirect('member_detail', org_slug=self.org.slug, pk=member.pk)


class RejectApplicationView(OrgAdminMixin, View):
    required_feature = 'applications'

    def post(self, request, org_slug, pk):
        from django.utils import timezone
        from .models import MemberApplication
        app = get_object_or_404(MemberApplication, pk=pk, organisation=self.org)
        app.status = MemberApplication.Status.REJECTED
        app.decided_at = timezone.now()
        app.save(update_fields=['status', 'decided_at'])
        messages.success(request, f"Application from {app.name} rejected.")
        return redirect('application_list', org_slug=self.org.slug)


class AddMemberNoteView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        member = get_object_or_404(Member, pk=pk, organisation=self.org)
        body = request.POST.get('body', '').strip()
        if body:
            from .models import MemberNote
            MemberNote.objects.create(member=member, author=request.user, body=body)
        return redirect_to_member(self.org.slug, pk, 'notes')


class DeleteMemberNoteView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk, note_pk):
        member = get_object_or_404(Member, pk=pk, organisation=self.org)
        from .models import MemberNote
        note = get_object_or_404(MemberNote, pk=note_pk, member=member)
        note.delete()
        return redirect_to_member(self.org.slug, pk, 'notes')


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
        # (header, value) per column; switched-off fields and features are left out.
        org = self.org
        columns = [
            ('Name', lambda m, g: m.name),
            ('Date of birth', lambda m, g: m.date_of_birth.isoformat() if m.date_of_birth else '')
            if org.has_field('date_of_birth') else None,
            ('Email', lambda m, g: m.email),
            ('Phone', lambda m, g: m.phone) if org.has_field('phone') else None,
            ('Joined date', lambda m, g: m.joined_date.isoformat() if m.joined_date else '')
            if org.has_field('joined_date') else None,
            ('Active', lambda m, g: 'Yes' if m.is_active else 'No'),
            ('Monthly fee', lambda m, g: str(m.monthly_fee) if m.monthly_fee else '')
            if org.has_feature('billing') else None,
            ('Guardian name', lambda m, g: g.name if g else '') if org.has_field('guardians') else None,
            ('Guardian email', lambda m, g: g.email if g else '') if org.has_field('guardians') else None,
            ('Guardian phone', lambda m, g: g.phone if g else '') if org.has_field('guardians') else None,
            ('Classes', lambda m, g: ', '.join(e.assigned_class.name for e in m.enrolments.all())),
        ]
        columns = [c for c in columns if c]
        writer = csv.writer(response)
        writer.writerow([header for header, _ in columns])
        for m in members:
            guardian = m.guardians.first()
            writer.writerow([value(m, guardian) for _, value in columns])
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

            # Columns for switched-off fields are ignored.
            rows.append({
                'name': name,
                'email': (row.get('email') or '').strip(),
                'phone': (row.get('phone') or '').strip() if self.org.has_field('phone') else '',
                'date_of_birth': dob if self.org.has_field('date_of_birth') else None,
                'joined_date': joined if self.org.has_field('joined_date') else None,
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
    required_feature = 'member_portal'

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


class RegeneratePortalTokenView(OrgAdminMixin, View):
    """Invalidates the member's existing portal link and issues a new one — use if a link may have leaked."""
    required_feature = 'member_portal'

    def post(self, request, org_slug, pk):
        from django.utils import timezone
        from .models import generate_token
        member = get_object_or_404(Member, pk=pk, organisation=self.org)
        member.token = generate_token()
        member.token_created_at = timezone.now()
        member.save(update_fields=['token', 'token_created_at'])
        messages.success(request, f"{member.name}'s portal link has been reset. The old link no longer works — send them the new one.")
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
