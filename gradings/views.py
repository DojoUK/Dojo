from datetime import date

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView

from dojo.mixins import OrgMixin
from members.models import Member

from .models import Grading, GradingAttendance, GradingSession


class GradingListView(OrgMixin, View):
    """
    List + create gradings. Uses OrgMixin (not OrgAdminMixin) — gradings are
    a shared, org-wide activity visible and manageable by any staff member,
    not restricted to org admins the way class management is.
    """
    def get(self, request, org_slug):
        gradings = Grading.objects.filter(organisation=self.org).order_by('name')
        return render(request, 'gradings/list.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'gradings': gradings,
        })

    def post(self, request, org_slug):
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        if not name:
            messages.error(request, 'Name is required.')
            return redirect('grading_list', org_slug=self.org.slug)

        grading = Grading.objects.create(organisation=self.org, name=name, description=description)
        messages.success(request, f'{grading.name} created.')
        return redirect('grading_detail', org_slug=self.org.slug, pk=grading.pk)


class GradingDetailView(OrgMixin, DetailView):
    """
    Top level of a grading: just its list of sessions. Judoka are enrolled
    per session (see GradingSessionDetailView), not here — who's up for
    grading can differ from one session to the next:

        Winter Grading 2026
        > Session 1
            > Judoka A
            > Judoka B
        > Session 2
    """
    template_name = 'gradings/detail.html'
    context_object_name = 'grading'

    def get_object(self):
        return get_object_or_404(Grading, pk=self.kwargs['pk'], organisation=self.org)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['sessions'] = self.object.sessions.order_by('-date')[:15]
        context['today'] = date.today()
        return context


class AddGradingSessionView(OrgMixin, View):
    def post(self, request, org_slug, pk):
        grading = get_object_or_404(Grading, pk=pk, organisation=self.org)
        name = request.POST.get('name', '').strip()
        date_raw = request.POST.get('date', '').strip()
        notes = request.POST.get('notes', '').strip()

        if not date_raw:
            messages.error(request, 'Date is required.')
            return redirect('grading_detail', org_slug=self.org.slug, pk=grading.pk)
        try:
            session_date = date.fromisoformat(date_raw)
        except ValueError:
            messages.error(request, 'Invalid date.')
            return redirect('grading_detail', org_slug=self.org.slug, pk=grading.pk)

        # No uniqueness check on (grading, date) — multiple named sessions
        # (e.g. "Morning" and "Afternoon") can share the same date.
        session = GradingSession.objects.create(
            grading=grading, date=session_date, name=name, notes=notes,
        )
        messages.success(request, f'{session.display_name()} added.')
        return redirect('grading_session_detail', org_slug=self.org.slug, pk=grading.pk, session_pk=session.pk)


class CancelGradingSessionView(OrgMixin, View):
    def post(self, request, org_slug, pk, session_pk):
        grading = get_object_or_404(Grading, pk=pk, organisation=self.org)
        session = get_object_or_404(GradingSession, pk=session_pk, grading=grading)
        session.is_cancelled = not session.is_cancelled
        session.save(update_fields=['is_cancelled'])
        messages.success(request, f'Session marked as {"cancelled" if session.is_cancelled else "active"}.')
        return redirect('grading_detail', org_slug=self.org.slug, pk=grading.pk)


class GradingSessionDetailView(OrgMixin, View):
    """
    The 2nd-level page: one grading session, where judoka are enrolled for
    that specific session (mirrors the enrol/unenrol pattern on the class
    detail page, just scoped to a session instead of a whole class).
    Enrolling here creates the GradingAttendance row the register page
    later fills in (present + new belt).
    """
    def get(self, request, org_slug, pk, session_pk):
        grading = get_object_or_404(Grading, pk=pk, organisation=self.org)
        session = get_object_or_404(GradingSession, pk=session_pk, grading=grading)

        enrolled_ids = list(
            GradingAttendance.objects.filter(session=session).values_list('member_id', flat=True)
        )
        enrolled = Member.objects.filter(pk__in=enrolled_ids).order_by('name')
        available = (
            Member.objects.filter(organisation=self.org, is_active=True)
            .exclude(pk__in=enrolled_ids)
            .order_by('name')
        )
        return render(request, 'gradings/session_detail.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'grading': grading,
            'session': session,
            'enrolled': enrolled,
            'available': available,
        })


class EnrolMemberView(OrgMixin, View):
    def post(self, request, org_slug, pk, session_pk):
        grading = get_object_or_404(Grading, pk=pk, organisation=self.org)
        session = get_object_or_404(GradingSession, pk=session_pk, grading=grading)
        member = get_object_or_404(Member, pk=request.POST.get('member_id'), organisation=self.org)
        _, created = GradingAttendance.objects.get_or_create(session=session, member=member)
        if created:
            messages.success(request, f'{member.name} added to this grading session.')
        else:
            messages.info(request, f'{member.name} is already enrolled in this session.')
        return redirect('grading_session_detail', org_slug=self.org.slug, pk=grading.pk, session_pk=session.pk)


class UnenrolMemberView(OrgMixin, View):
    def post(self, request, org_slug, pk, session_pk, member_pk):
        grading = get_object_or_404(Grading, pk=pk, organisation=self.org)
        session = get_object_or_404(GradingSession, pk=session_pk, grading=grading)
        member = get_object_or_404(Member, pk=member_pk, organisation=self.org)
        GradingAttendance.objects.filter(session=session, member=member).delete()
        messages.success(request, f'{member.name} removed from this grading session.')
        return redirect('grading_session_detail', org_slug=self.org.slug, pk=grading.pk, session_pk=session.pk)


class GradingRegisterView(OrgMixin, View):
    """
    The grading register: mark attendance for a grading session's already-
    enrolled judoka (see GradingSessionDetailView) and, for anyone who
    passed, commit their new belt/stage straight into the progression
    system (creates a real `progression.MemberProgression` row, the same
    record `RecordPromotionView` creates from the member profile).
    """
    def _get_grading_session(self, pk, session_pk):
        grading = get_object_or_404(Grading, pk=pk, organisation=self.org)
        session = get_object_or_404(GradingSession, pk=session_pk, grading=grading)
        return grading, session

    def _render(self, request, grading, session):
        from progression.models import MemberProgression, ProgressionSystem

        attendance_qs = (
            GradingAttendance.objects.filter(session=session)
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

        return render(request, 'gradings/register.html', {
            'org': self.org,
            'org_membership': self.org_membership,
            'grading': grading,
            'session': session,
            'rows': rows,
            'systems': systems,
        })

    def get(self, request, org_slug, pk, session_pk):
        grading, session = self._get_grading_session(pk, session_pk)
        return self._render(request, grading, session)

    def post(self, request, org_slug, pk, session_pk):
        from progression.models import MemberProgression, ProgressionStage

        grading, session = self._get_grading_session(pk, session_pk)
        attendance_qs = GradingAttendance.objects.filter(session=session).select_related('member', 'progression')
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
                            notes=f'Graded at "{grading.name}" ({session.date:%d %b %Y})',
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
            messages.success(request, f'Grading saved. Promoted: {", ".join(promoted)}.')
        else:
            messages.success(request, f'Grading saved for {session.date:%d %b %Y}.')
        return redirect('grading_register', org_slug=self.org.slug, pk=grading.pk, session_pk=session.pk)
