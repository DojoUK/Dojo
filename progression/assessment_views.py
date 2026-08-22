from datetime import date

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView

from dojo.mixins import OrgMixin
from members.models import Member

from .models import (
    Assessment, AssessmentAttendance, AssessmentSession,
    MemberProgression, ProgressionStage, ProgressionSystem,
)


class AssessmentListView(OrgMixin, View):
    """
    List + create assessments. Uses OrgMixin (not OrgAdminMixin) —
    assessments are a shared, org-wide activity visible and manageable by
    any staff member, not restricted to org admins the way class management
    is.
    """
    def get(self, request, org_slug):
        assessments = Assessment.objects.filter(organisation=self.org).order_by('name')
        return render(request, 'progression/assessments/list.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'assessments': assessments,
        })

    def post(self, request, org_slug):
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        if not name:
            messages.error(request, 'Name is required.')
            return redirect('assessment_list', org_slug=self.org.slug)

        assessment = Assessment.objects.create(organisation=self.org, name=name, description=description)
        messages.success(request, f'{assessment.name} created.')
        return redirect('assessment_detail', org_slug=self.org.slug, pk=assessment.pk)


class AssessmentDetailView(OrgMixin, DetailView):
    """
    Top level of an assessment: just its list of sessions. Members are
    enrolled per session (see AssessmentSessionDetailView), not here — who's
    being assessed can differ from one session to the next.
    """
    template_name = 'progression/assessments/detail.html'
    context_object_name = 'assessment'

    def get_object(self):
        return get_object_or_404(Assessment, pk=self.kwargs['pk'], organisation=self.org)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['sessions'] = self.object.sessions.order_by('-date')[:15]
        context['today'] = date.today()
        return context


class AddAssessmentSessionView(OrgMixin, View):
    def post(self, request, org_slug, pk):
        assessment = get_object_or_404(Assessment, pk=pk, organisation=self.org)
        name = request.POST.get('name', '').strip()
        date_raw = request.POST.get('date', '').strip()
        notes = request.POST.get('notes', '').strip()

        if not date_raw:
            messages.error(request, 'Date is required.')
            return redirect('assessment_detail', org_slug=self.org.slug, pk=assessment.pk)
        try:
            session_date = date.fromisoformat(date_raw)
        except ValueError:
            messages.error(request, 'Invalid date.')
            return redirect('assessment_detail', org_slug=self.org.slug, pk=assessment.pk)

        # No uniqueness check on (assessment, date) — multiple named sessions
        # (e.g. "Primary" and "Junior") can share the same date.
        session = AssessmentSession.objects.create(
            assessment=assessment, date=session_date, name=name, notes=notes,
        )
        messages.success(request, f'{session.display_name()} added.')
        return redirect('assessment_session_detail', org_slug=self.org.slug, pk=assessment.pk, session_pk=session.pk)


class CancelAssessmentSessionView(OrgMixin, View):
    def post(self, request, org_slug, pk, session_pk):
        assessment = get_object_or_404(Assessment, pk=pk, organisation=self.org)
        session = get_object_or_404(AssessmentSession, pk=session_pk, assessment=assessment)
        session.is_cancelled = not session.is_cancelled
        session.save(update_fields=['is_cancelled'])
        messages.success(request, f'Session marked as {"cancelled" if session.is_cancelled else "active"}.')
        return redirect('assessment_detail', org_slug=self.org.slug, pk=assessment.pk)


class AssessmentSessionDetailView(OrgMixin, View):
    """
    The 2nd-level page: one assessment session, where members are enrolled
    for that specific session (mirrors the enrol/unenrol pattern on the
    class detail page, just scoped to a session instead of a whole class).
    Enrolling here creates the AssessmentAttendance row the register page
    later fills in (present + new stage).
    """
    def get(self, request, org_slug, pk, session_pk):
        assessment = get_object_or_404(Assessment, pk=pk, organisation=self.org)
        session = get_object_or_404(AssessmentSession, pk=session_pk, assessment=assessment)

        enrolled_ids = list(
            AssessmentAttendance.objects.filter(session=session).values_list('member_id', flat=True)
        )
        enrolled = Member.objects.filter(pk__in=enrolled_ids).order_by('name')
        available = (
            Member.objects.filter(organisation=self.org, is_active=True)
            .exclude(pk__in=enrolled_ids)
            .order_by('name')
        )
        return render(request, 'progression/assessments/session_detail.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'assessment': assessment,
            'session': session,
            'enrolled': enrolled,
            'available': available,
        })


class EnrolAssessmentMemberView(OrgMixin, View):
    def post(self, request, org_slug, pk, session_pk):
        assessment = get_object_or_404(Assessment, pk=pk, organisation=self.org)
        session = get_object_or_404(AssessmentSession, pk=session_pk, assessment=assessment)
        member = get_object_or_404(Member, pk=request.POST.get('member_id'), organisation=self.org)
        _, created = AssessmentAttendance.objects.get_or_create(session=session, member=member)
        if created:
            messages.success(request, f'{member.name} added to this session.')
        else:
            messages.info(request, f'{member.name} is already enrolled in this session.')
        return redirect('assessment_session_detail', org_slug=self.org.slug, pk=assessment.pk, session_pk=session.pk)


class UnenrolAssessmentMemberView(OrgMixin, View):
    def post(self, request, org_slug, pk, session_pk, member_pk):
        assessment = get_object_or_404(Assessment, pk=pk, organisation=self.org)
        session = get_object_or_404(AssessmentSession, pk=session_pk, assessment=assessment)
        member = get_object_or_404(Member, pk=member_pk, organisation=self.org)
        AssessmentAttendance.objects.filter(session=session, member=member).delete()
        messages.success(request, f'{member.name} removed from this session.')
        return redirect('assessment_session_detail', org_slug=self.org.slug, pk=assessment.pk, session_pk=session.pk)


class AssessmentRegisterView(OrgMixin, View):
    """
    The assessment register: mark attendance for a session's already-
    enrolled members (see AssessmentSessionDetailView) and, for anyone who
    passed, commit their new stage straight into the progression system
    (creates a real MemberProgression row, the same record
    RecordPromotionView creates from the member profile).
    """
    def _get_assessment_session(self, pk, session_pk):
        assessment = get_object_or_404(Assessment, pk=pk, organisation=self.org)
        session = get_object_or_404(AssessmentSession, pk=session_pk, assessment=assessment)
        return assessment, session

    def _render(self, request, assessment, session):
        attendance_qs = (
            AssessmentAttendance.objects.filter(session=session)
            .select_related('member', 'new_stage')
            .order_by('member__name')
        )
        member_ids = [a.member_id for a in attendance_qs]

        latest_progression = {}
        for p in (
            MemberProgression.objects.filter(member_id__in=member_ids)
            .select_related('stage')
            .order_by('member_id', '-achieved_date')
        ):
            latest_progression.setdefault(p.member_id, p)

        rows = []
        for att in attendance_qs:
            rows.append({
                'member': att.member,
                'present': att.present,
                'current_grade': latest_progression.get(att.member_id),
                'selected_stage_id': att.new_stage_id,
                'awarded_stage': att.new_stage,
            })

        systems = ProgressionSystem.objects.filter(organisation=self.org).prefetch_related('stages')

        return render(request, 'progression/assessments/register.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'assessment': assessment,
            'session': session,
            'rows': rows,
            'systems': systems,
        })

    def get(self, request, org_slug, pk, session_pk):
        assessment, session = self._get_assessment_session(pk, session_pk)
        return self._render(request, assessment, session)

    def post(self, request, org_slug, pk, session_pk):
        assessment, session = self._get_assessment_session(pk, session_pk)
        attendance_qs = AssessmentAttendance.objects.filter(session=session).select_related('member', 'progression')
        present_ids = {int(x) for x in request.POST.getlist('present')}

        promoted = []
        for att in attendance_qs:
            member = att.member
            att.present = member.pk in present_ids

            stage_id_raw = (request.POST.get(f'stage_{member.pk}') or '').strip()
            if stage_id_raw:
                try:
                    stage_id = int(stage_id_raw)
                except ValueError:
                    stage_id = None
                if stage_id and stage_id != att.new_stage_id:
                    stage = ProgressionStage.objects.filter(pk=stage_id, system__organisation=self.org).first()
                    if stage:
                        if att.progression_id:
                            att.progression.delete()
                        progression = MemberProgression.objects.create(
                            member=member,
                            stage=stage,
                            achieved_date=session.date,
                            notes=f'Awarded at "{assessment.name}" — {session.display_name()} ({session.date:%d %b %Y})',
                        )
                        att.new_stage = stage
                        att.progression = progression
                        promoted.append(f'{member.name} → {stage.name}')
            else:
                if att.progression_id:
                    att.progression.delete()
                att.new_stage = None
                att.progression = None

            att.save()

        session.notes = request.POST.get('notes', session.notes)
        session.save(update_fields=['notes'])

        if promoted:
            messages.success(request, f'Register saved. Promoted: {", ".join(promoted)}.')
        else:
            messages.success(request, f'Register saved for {session.date:%d %b %Y}.')
        return redirect('assessment_register', org_slug=self.org.slug, pk=assessment.pk, session_pk=session.pk)
