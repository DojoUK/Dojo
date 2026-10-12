from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from classes.models import Attendance, Class, Session
from members.models import Member
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember
from progression import gradings
from progression.models import (
    Grading,
    GradingCandidate,
    GradingSession,
    MemberProgression,
    ProgressionStage,
    ProgressionSystem,
)

ALL_ON = {key: True for key in FEATURES_BY_KEY}
PASSED = GradingCandidate.Result.PASSED
FAILED = GradingCandidate.Result.FAILED


class GradingTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON))
        cls.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')

        cls.kyu = ProgressionSystem.objects.create(organisation=cls.org, name='Kyu', uses_gradings=True)
        cls.white = ProgressionStage.objects.create(system=cls.kyu, name='White', order=0)
        cls.yellow = ProgressionStage.objects.create(system=cls.kyu, name='Yellow', order=1)
        cls.orange = ProgressionStage.objects.create(system=cls.kyu, name='Orange', order=2)
        cls.bjj = ProgressionSystem.objects.create(organisation=cls.org, name='BJJ', uses_gradings=False)

        cls.alice = Member.objects.create(organisation=cls.org, name='Alice A')
        cls.bob = Member.objects.create(organisation=cls.org, name='Bob B')
        cls.gone = Member.objects.create(organisation=cls.org, name='Gone G', is_active=False)
        cls.alice_yellow = MemberProgression.objects.create(
            member=cls.alice, stage=cls.yellow, achieved_date=date(2026, 1, 10),
        )

        cls.grading = Grading.objects.create(organisation=cls.org, name='Winter grading', system=cls.kyu)
        cls.session = GradingSession.objects.create(grading=cls.grading, date=date(2026, 12, 12))

    def setUp(self):
        self.client.force_login(self.admin)

    def url(self, name, **kwargs):
        return reverse(name, kwargs={'org_slug': self.org.slug, **kwargs})

    def session_url(self, name, session=None, **kwargs):
        session = session or self.session
        return self.url(name, pk=session.grading_id, session_pk=session.pk, **kwargs)

    def candidate(self, member, session=None, target=None):
        return GradingCandidate.objects.create(
            session=session or self.session, member=member, target_stage=target,
        )


class GradingRulesTests(GradingTestBase):
    def test_next_stage(self):
        self.assertEqual(self.white.next_stage(), self.yellow)
        self.assertIsNone(self.orange.next_stage())

    def test_candidates_go_for_their_next_grade(self):
        added = gradings.add_candidates(self.session, [self.alice, self.bob])
        self.assertEqual(added, 2)
        targets = {c.member: c.target_stage for c in self.session.candidates.all()}
        self.assertEqual(targets, {self.alice: self.orange, self.bob: self.white})

    def test_a_member_is_graded_once_per_grading(self):
        other = GradingSession.objects.create(grading=self.grading, date=self.session.date, name='Seniors')
        gradings.add_candidates(self.session, [self.alice])
        self.assertEqual(gradings.add_candidates(other, [self.alice, self.bob]), 1)
        self.assertFalse(other.candidates.filter(member=self.alice).exists())

    def test_eligibility_counts_sessions_since_current_grade(self):
        cls = Class.objects.create(organisation=self.org, name='Juniors')
        for days_after, member in ((-5, self.alice), (5, self.alice), (6, self.alice), (5, self.bob)):
            session, _ = Session.objects.get_or_create(
                assigned_class=cls, date=self.alice_yellow.achieved_date + timedelta(days=days_after),
            )
            Attendance.objects.create(session=session, member=member, present=True)

        rows = gradings.eligibility_rows(self.session)
        by_member = {r['member']: r for r in rows}
        self.assertEqual(set(by_member), {self.alice, self.bob})  # inactive members left out
        self.assertEqual(by_member[self.alice]['attended_since'], 2)  # not the session before Yellow
        self.assertEqual(by_member[self.alice]['current'], self.alice_yellow)
        self.assertEqual(by_member[self.bob]['attended_since'], 1)
        self.assertEqual(rows[0]['member'], self.alice)

        gradings.add_candidates(self.session, [self.alice])
        self.assertEqual([r['member'] for r in gradings.eligibility_rows(self.session)], [self.bob])


class ResultsTests(GradingTestBase):
    def save(self, candidate, result, stage=None, notes=''):
        return self.client.post(self.session_url('grading_results_save'), {
            f'result_{candidate.pk}': result,
            f'stage_{candidate.pk}': stage.pk if stage else '',
            f'notes_{candidate.pk}': notes,
        })

    def test_a_pass_adds_to_the_members_grade_history(self):
        candidate = self.candidate(self.alice, target=self.orange)
        self.save(candidate, PASSED, self.orange, notes='Strong ukemi')
        candidate.refresh_from_db()
        progression = candidate.progression
        self.assertEqual(progression.member, self.alice)
        self.assertEqual(progression.stage, self.orange)
        self.assertEqual(progression.achieved_date, self.session.date)
        self.assertIn('Winter grading', progression.notes)
        self.assertEqual(candidate.notes, 'Strong ukemi')

    def test_changing_the_grade_updates_the_same_record(self):
        candidate = self.candidate(self.alice, target=self.orange)
        self.save(candidate, PASSED, self.orange)
        self.save(candidate, PASSED, self.white)
        self.assertEqual(MemberProgression.objects.filter(member=self.alice).count(), 2)  # Yellow + this one
        candidate.refresh_from_db()
        self.assertEqual(candidate.progression.stage, self.white)

    def test_changing_a_pass_to_a_fail_removes_the_grade(self):
        candidate = self.candidate(self.alice, target=self.orange)
        self.save(candidate, PASSED, self.orange)
        self.save(candidate, FAILED, self.orange)
        candidate.refresh_from_db()
        self.assertIsNone(candidate.progression)
        self.assertEqual(list(MemberProgression.objects.filter(member=self.alice)), [self.alice_yellow])

    def test_a_pass_without_a_grade_is_not_saved(self):
        candidate = self.candidate(self.bob)
        response = self.save(candidate, PASSED, None, notes='ignored')
        candidate.refresh_from_db()
        self.assertEqual(candidate.result, GradingCandidate.Result.PENDING)
        self.assertEqual(candidate.notes, '')
        self.assertFalse(MemberProgression.objects.filter(member=self.bob).exists())
        self.assertEqual(response.status_code, 302)

    def test_session_page_shows_the_grade_held_before_the_grading(self):
        candidate = self.candidate(self.alice, target=self.orange)
        self.save(candidate, PASSED, self.orange)
        response = self.client.get(self.url('grading_detail', pk=self.grading.pk))
        shown = response.context['candidates'][0]
        self.assertEqual(shown.current, self.alice_yellow)

    def test_moving_the_session_date_moves_the_grade_date(self):
        candidate = self.candidate(self.alice, target=self.orange)
        self.save(candidate, PASSED, self.orange)
        new_date = date(2026, 12, 13)
        self.client.post(self.session_url('grading_session_edit'), {'name': '', 'date': new_date.isoformat()})
        candidate.refresh_from_db()
        self.assertEqual(candidate.progression.achieved_date, new_date)

    def test_cannot_remove_someone_with_a_pass(self):
        candidate = self.candidate(self.alice, target=self.orange)
        self.save(candidate, PASSED, self.orange)
        self.client.post(self.session_url('grading_candidate_remove', candidate_pk=candidate.pk))
        self.assertTrue(GradingCandidate.objects.filter(pk=candidate.pk).exists())

        pending = self.candidate(self.bob)
        self.client.post(self.session_url('grading_candidate_remove', candidate_pk=pending.pk))
        self.assertFalse(GradingCandidate.objects.filter(pk=pending.pk).exists())

    def test_cannot_delete_a_grading_with_passes(self):
        candidate = self.candidate(self.alice, target=self.orange)
        self.save(candidate, PASSED, self.orange)
        self.client.post(self.url('grading_delete', pk=self.grading.pk))
        self.assertTrue(Grading.objects.filter(pk=self.grading.pk).exists())

        self.save(candidate, FAILED, self.orange)
        self.client.post(self.url('grading_delete', pk=self.grading.pk))
        self.assertFalse(Grading.objects.filter(pk=self.grading.pk).exists())


class GradingPagesTests(GradingTestBase):
    def test_creating_a_grading_makes_one_session(self):
        self.client.post(self.url('grading_list'), {'name': 'Summer', 'date': '2027-06-01', 'system_id': self.kyu.pk})
        grading = Grading.objects.get(name='Summer')
        self.assertEqual(list(grading.sessions.values_list('date', flat=True)), [date(2027, 6, 1)])

    def test_only_systems_using_gradings_can_be_chosen(self):
        self.client.post(self.url('grading_list'), {'name': 'BJJ day', 'date': '2027-06-01', 'system_id': self.bjj.pk})
        self.assertFalse(Grading.objects.filter(name='BJJ day').exists())
        response = self.client.get(self.url('grading_list'))
        self.assertEqual(list(response.context['systems']), [self.kyu])

    def test_single_session_grading_hides_the_session_level(self):
        response = self.client.get(self.url('grading_detail', pk=self.grading.pk))
        self.assertTemplateUsed(response, 'progression/gradings/session.html')
        self.assertTrue(response.context['single'])
        self.assertRedirects(
            self.client.get(self.session_url('grading_session')),
            self.url('grading_detail', pk=self.grading.pk),
        )

    def test_several_sessions_get_an_overview(self):
        self.client.post(self.url('grading_session_add', pk=self.grading.pk), {'name': 'Seniors', 'date': '2026-12-12'})
        response = self.client.get(self.url('grading_detail', pk=self.grading.pk))
        self.assertTemplateUsed(response, 'progression/gradings/detail.html')
        self.assertEqual(len(response.context['sessions']), 2)
        seniors = self.grading.sessions.get(name='Seniors')
        response = self.client.get(self.session_url('grading_session', session=seniors))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['single'])

    def test_last_session_cannot_be_deleted(self):
        self.client.post(self.session_url('grading_session_delete'))
        self.assertTrue(GradingSession.objects.filter(pk=self.session.pk).exists())

    def test_adding_people_from_the_tick_list(self):
        self.client.post(self.session_url('grading_candidates_add'), {'member_ids': [self.alice.pk, self.bob.pk]})
        self.assertEqual(set(self.session.candidates.values_list('member', flat=True)), {self.alice.pk, self.bob.pk})

    def test_system_toggle_on_progression_settings(self):
        self.client.post(self.url('progression_system_toggle_gradings', pk=self.bjj.pk))
        self.bjj.refresh_from_db()
        self.assertTrue(self.bjj.uses_gradings)


class GradingAccessTests(GradingTestBase):
    """Tenant-matrix rows for the grading routes."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.coach_user = User.objects.create_user('coach', password='pw')
        cls.coach = OrganisationMember.objects.create(user=cls.coach_user, organisation=cls.org, role='coach')
        cls.org_b = Organisation.objects.create(name='Other', slug='other', features=dict(ALL_ON))
        cls.admin_b = User.objects.create_user('admin-b', password='pw')
        OrganisationMember.objects.create(user=cls.admin_b, organisation=cls.org_b, role='org_admin')
        system_b = ProgressionSystem.objects.create(organisation=cls.org_b, name='Kyu', uses_gradings=True)
        cls.stage_b = ProgressionStage.objects.create(system=system_b, name='SENTINEL-B-belt')
        cls.member_b = Member.objects.create(organisation=cls.org_b, name='SENTINEL-B-member')
        cls.grading_b = Grading.objects.create(organisation=cls.org_b, name='SENTINEL-B-grading', system=system_b)
        cls.session_b = GradingSession.objects.create(grading=cls.grading_b, date=date(2026, 12, 12))
        cls.candidate_b = GradingCandidate.objects.create(session=cls.session_b, member=cls.member_b)

    def routes(self):
        candidate = GradingCandidate.objects.get_or_create(session=self.session, member=self.bob)[0]
        return [
            ('get', self.url('grading_list')),
            ('get', self.url('grading_detail', pk=self.grading.pk)),
            ('post', self.url('grading_edit', pk=self.grading.pk)),
            ('post', self.url('grading_session_add', pk=self.grading.pk)),
            ('get', self.session_url('grading_session')),
            ('post', self.session_url('grading_session_edit')),
            ('post', self.session_url('grading_candidates_add')),
            ('post', self.session_url('grading_results_save')),
            ('post', self.session_url('grading_candidate_remove', candidate_pk=candidate.pk)),
            ('post', self.session_url('grading_session_delete')),
            ('post', self.url('grading_delete', pk=self.grading.pk)),
        ]

    def assert_refused(self, status):
        for method, route in self.routes():
            with self.subTest(route=route):
                response = getattr(self.client, method)(route)
                self.assertEqual(response.status_code, status)
                self.assertNotContains(response, 'Alice A', status_code=status)
        self.assertTrue(Grading.objects.filter(pk=self.grading.pk, name='Winter grading').exists())
        self.assertEqual(GradingSession.objects.filter(grading=self.grading).count(), 1)

    def test_coach_without_permission_is_refused(self):
        self.client.force_login(self.coach_user)
        self.assert_refused(403)

    def test_coach_with_manage_gradings_can_run_gradings(self):
        self.coach.can_manage_gradings = True
        self.coach.save()
        self.client.force_login(self.coach_user)
        self.assertEqual(self.client.get(self.url('grading_detail', pk=self.grading.pk)).status_code, 200)
        self.assertContains(self.client.get(self.url('org_dashboard')), self.url('grading_list'))

    def test_other_org_admin_is_refused(self):
        self.client.force_login(self.admin_b)
        self.assert_refused(403)

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        for method, route in self.routes():
            with self.subTest(route=route):
                response = getattr(self.client, method)(route)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response['Location'])

    def test_feature_off_is_404(self):
        self.org.set_features({**ALL_ON, 'gradings': False})
        self.org.save(update_fields=['features'])
        self.assert_refused(404)

    def test_other_org_objects_are_refused(self):
        self.assertEqual(self.client.get(self.url('grading_detail', pk=self.grading_b.pk)).status_code, 404)
        response = self.client.post(
            self.url('grading_results_save', pk=self.grading_b.pk, session_pk=self.session_b.pk),
            {f'result_{self.candidate_b.pk}': PASSED, f'stage_{self.candidate_b.pk}': self.stage_b.pk},
        )
        self.assertEqual(response.status_code, 404)
        # B's session under A's grading
        response = self.client.post(self.url('grading_candidates_add', pk=self.grading.pk, session_pk=self.session_b.pk))
        self.assertEqual(response.status_code, 404)
        # B's candidate under A's session
        response = self.client.post(self.session_url('grading_candidate_remove', candidate_pk=self.candidate_b.pk))
        self.assertEqual(response.status_code, 404)
        # B's member and B's stage posted to A's routes
        self.client.post(self.session_url('grading_candidates_add'), {'member_ids': [self.member_b.pk]})
        self.assertFalse(self.session.candidates.filter(member=self.member_b).exists())
        candidate = self.candidate(self.alice)
        self.client.post(self.session_url('grading_results_save'), {
            f'result_{candidate.pk}': PASSED, f'stage_{candidate.pk}': self.stage_b.pk,
        })
        candidate.refresh_from_db()
        self.assertIsNone(candidate.progression)
        self.assertNotContains(self.client.get(self.url('grading_detail', pk=self.grading.pk)), 'SENTINEL-B')

        self.candidate_b.refresh_from_db()
        self.assertEqual(self.candidate_b.result, GradingCandidate.Result.PENDING)
        self.assertTrue(GradingCandidate.objects.filter(pk=self.candidate_b.pk).exists())
        self.assertFalse(MemberProgression.objects.filter(member=self.member_b).exists())
