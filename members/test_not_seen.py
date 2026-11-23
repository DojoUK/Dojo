from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from classes.models import Attendance, Class, ClassMember, Session
from members import not_seen
from members.models import Member, MemberLeavingRequest
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember

ALL_ON = {key: True for key in FEATURES_BY_KEY}


class NotSeenTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON),
                                               settings={'two_factor': 'optional'})
        self.cls = Class.objects.create(organisation=self.org, name='Adults', is_active=True)
        self.today = timezone.localdate()
        self.sessions = {}
        old = self.today - timedelta(days=60)
        self.regular = self.member('Rita Regular', joined=old)
        self.gone = self.member('Gary Gone', joined=old)
        self.slowing = self.member('Sam Slowing', joined=old)
        self.never = self.member('Nina Never', joined=self.today - timedelta(days=40))
        self.new = self.member('Newt New', joined=self.today - timedelta(days=3))
        for days_ago in range(2, 110, 7):                 # weekly sessions for ~15 weeks
            self.attend(self.regular, days_ago)
        for days_ago in (40, 47, 54):
            self.attend(self.gone, days_ago)
        for days_ago in [2] + list(range(31, 110, 3)):    # ~every 3 days until a month ago, once since
            self.attend(self.slowing, days_ago)

    def member(self, name, joined):
        m = Member.objects.create(organisation=self.org, name=name, email=f'{name.split()[0].lower()}@example.com',
                                  joined_date=joined)
        ClassMember.objects.create(assigned_class=self.cls, member=m)
        return m

    def attend(self, member, days_ago):
        date = self.today - timedelta(days=days_ago)
        session = self.sessions.get(date) or Session.objects.create(assigned_class=self.cls, date=date)
        self.sessions[date] = session
        Attendance.objects.create(session=session, member=member, present=True)

    def names(self, rows):
        return [r['member'].name for r in rows]

    def test_groups(self):
        found = not_seen.report(self.org, days=21)
        self.assertEqual(self.names(found['not_seen']), ['Gary Gone'])
        self.assertEqual(found['not_seen'][0]['days_since'], 40)
        self.assertEqual(self.names(found['slowing']), ['Sam Slowing'])
        self.assertEqual(found['slowing'][0]['recent'], 1)
        self.assertGreaterEqual(found['slowing'][0]['usual'], 8)
        self.assertEqual(self.names(found['never']), ['Nina Never'])   # Newt only joined 3 days ago
        self.assertEqual(self.names(not_seen.report(self.org, days=60)['not_seen']), [])

    def test_archived_and_inactive_members_are_left_out(self):
        self.gone.is_active = False
        self.gone.save()
        self.assertEqual(not_seen.report(self.org)['not_seen'], [])

    def test_page_csv_and_email_selected(self):
        MemberLeavingRequest.objects.create(member=self.gone, reason='Moving')
        admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=admin, organisation=self.org, role='org_admin')
        self.client.force_login(admin)
        url = reverse('member_not_seen', kwargs={'org_slug': self.org.slug})
        page = self.client.get(url).content.decode()
        for text in ('Not seen in 21 days or more', 'Gary Gone', 'Asked to leave', 'Coming less often', 'Sam Slowing',
                     'Never come', 'Nina Never', 'Email selected'):
            self.assertTrue(text in page, text)
        self.assertFalse('Rita Regular' in page)
        csv = self.client.get(url, {'format': 'csv', 'days': '30'}).content.decode()
        self.assertIn('Not seen,Gary Gone', csv)
        response = self.client.post(reverse('member_bulk_action', kwargs={'org_slug': self.org.slug}),
                                    {'bulk_action': 'email', 'member_ids': [self.gone.pk, self.never.pk]})
        self.assertRedirects(response, reverse('member_list', kwargs={'org_slug': self.org.slug}), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse('member_list', kwargs={'org_slug': self.org.slug})), 'Email 2 members')

    def test_dashboard_tile_links_to_the_report(self):
        admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=admin, organisation=self.org, role='org_admin')
        self.client.force_login(admin)
        page = self.client.get(reverse('org_dashboard', kwargs={'org_slug': self.org.slug})).content.decode()
        self.assertIn(reverse('member_not_seen', kwargs={'org_slug': self.org.slug}) + '?days=14', page)

    def test_coaches_cant_see_it(self):
        coach = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=coach, organisation=self.org, role='coach')
        self.client.force_login(coach)
        response = self.client.get(reverse('member_not_seen', kwargs={'org_slug': self.org.slug}))
        self.assertNotEqual(response.status_code, 200)
