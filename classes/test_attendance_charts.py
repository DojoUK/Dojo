import json
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from classes.models import Attendance, Class, ClassCoach, ClassMember, Session
from members.models import Member
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember

ALL_ON = {key: True for key in FEATURES_BY_KEY}
TODAY = date.today()


class AttendanceChartBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON))
        cls.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')
        cls.coach = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=cls.coach, organisation=cls.org, role='coach')

        cls.juniors = Class.objects.create(organisation=cls.org, name='Juniors')
        cls.seniors = Class.objects.create(organisation=cls.org, name='Seniors')
        ClassCoach.objects.create(assigned_class=cls.seniors, user=cls.coach)
        cls.members = [Member.objects.create(organisation=cls.org, name=f'Member {i}') for i in range(4)]
        for m in cls.members:
            ClassMember.objects.create(assigned_class=cls.juniors, member=m)

        # Juniors: 12 weekly sessions up to last week, with 1, 2, 3, 4, 1, 2, ... present.
        cls.juniors_sessions = []
        for week in range(12, 0, -1):
            session = Session.objects.create(assigned_class=cls.juniors, date=TODAY - timedelta(weeks=week))
            cls.juniors_sessions.append(session)
            present = (12 - week) % 4 + 1
            for i, m in enumerate(cls.members):
                Attendance.objects.create(session=session, member=m, present=i < present)
        # Neither a cancelled session nor a future one counts.
        cancelled = Session.objects.create(assigned_class=cls.juniors, date=TODAY - timedelta(days=3), is_cancelled=True)
        Attendance.objects.create(session=cancelled, member=cls.members[0], present=True)
        Session.objects.create(assigned_class=cls.juniors, date=TODAY + timedelta(days=4))

        cls.seniors_session = Session.objects.create(assigned_class=cls.seniors, date=TODAY - timedelta(days=1))

    def url(self, name, **kwargs):
        return reverse(name, kwargs={'org_slug': self.org.slug, **kwargs})


class TrendChartTests(AttendanceChartBase):
    def setUp(self):
        self.client.force_login(self.admin)

    def get(self, **params):
        return self.client.get(self.url('attendance_analytics'), params).context

    def test_last_ten_sessions_oldest_first(self):
        context = self.get(trend_class=self.juniors.pk)
        # Weeks 10..1 ago: present counts 3,4,1,2,3,4,1,2,3,4
        self.assertEqual(json.loads(context['trend_data']), [3, 4, 1, 2, 3, 4, 1, 2, 3, 4])
        self.assertEqual(context['trend_average'], 2.7)
        self.assertEqual(
            json.loads(context['trend_labels'])[-1], (TODAY - timedelta(weeks=1)).strftime('%d %b'),
        )

    def test_longer_window(self):
        context = self.get(trend_class=self.juniors.pk, trend_count=25)
        self.assertEqual(len(json.loads(context['trend_data'])), 12)
        self.assertEqual(context['trend_average'], 2.5)  # 3 x (1+2+3+4) / 12

    def test_odd_window_falls_back_to_ten(self):
        self.assertEqual(self.get(trend_class=self.juniors.pk, trend_count=7)['trend_count'], 10)
        self.assertEqual(self.get(trend_class=self.juniors.pk, trend_count='x')['trend_count'], 10)

    def test_defaults_to_the_table_filter_then_the_first_class(self):
        self.assertEqual(self.get(**{'class': self.seniors.pk})['trend_selected_class'], str(self.seniors.pk))
        self.assertEqual(self.get()['trend_selected_class'], str(self.juniors.pk))

    def test_sessions_without_a_register_are_left_out(self):
        context = self.get(trend_class=self.seniors.pk)
        self.assertFalse(context['trend_has_data'])
        Attendance.objects.create(session=self.seniors_session, member=self.members[0], present=False)
        context = self.get(trend_class=self.seniors.pk)
        self.assertEqual(json.loads(context['trend_data']), [0])

    def test_class_with_no_sessions_run(self):
        empty = Class.objects.create(organisation=self.org, name='Zumba')
        context = self.get(trend_class=empty.pk)
        self.assertFalse(context['trend_has_data'])

    def test_page_draws_the_chart(self):
        response = self.client.get(self.url('attendance_analytics'), {'trend_class': self.juniors.pk})
        self.assertContains(response, 'id="attendanceTrendChart"')
        self.assertContains(response, 'chart.umd.min.js')


class TrendChartScopeTests(AttendanceChartBase):
    def test_coach_only_charts_their_own_classes(self):
        self.client.force_login(self.coach)
        context = self.client.get(self.url('attendance_analytics'), {'trend_class': self.juniors.pk}).context
        self.assertEqual(context['trend_selected_class'], str(self.seniors.pk))
        self.assertNotIn(self.juniors, list(context['classes']))

    def test_another_clubs_class_is_ignored(self):
        other = Organisation.objects.create(name='Other', slug='other', features=dict(ALL_ON))
        theirs = Class.objects.create(organisation=other, name='Theirs')
        self.client.force_login(self.admin)
        context = self.client.get(self.url('attendance_analytics'), {'trend_class': theirs.pk}).context
        self.assertEqual(context['trend_selected_class'], str(self.juniors.pk))


class MemberAttendanceCardTests(AttendanceChartBase):
    def test_member_page_shows_count_and_recent_sessions(self):
        self.client.force_login(self.admin)
        member = self.members[0]   # present at every session, plus the cancelled one
        response = self.client.get(self.url('member_detail', pk=member.pk))
        self.assertEqual(response.context['sessions_attended'], 13)
        recent = response.context['recent_attended']
        self.assertEqual(len(recent), 5)
        self.assertEqual(recent[0].session.date, TODAY - timedelta(days=3))
        self.assertContains(response, 'Recent attendance')

    def test_only_sessions_they_were_present_at(self):
        self.client.force_login(self.admin)
        member = self.members[3]   # present 3 times out of 12
        response = self.client.get(self.url('member_detail', pk=member.pk))
        self.assertEqual(response.context['sessions_attended'], 3)
        self.assertEqual(response.context['last_attended'], TODAY - timedelta(weeks=1))

    def test_never_attended(self):
        self.client.force_login(self.admin)
        newcomer = Member.objects.create(organisation=self.org, name='Newcomer')
        response = self.client.get(self.url('member_detail', pk=newcomer.pk))
        self.assertContains(response, 'No attendance recorded yet.')
