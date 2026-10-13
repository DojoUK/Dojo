from datetime import date, timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.emails import send_invoice_email
from billing.models import Invoice
from classes.models import Class, ClassMember, WaitingList
from members.models import Member
from organisations.features import FEATURES, FEATURES_BY_KEY, effective_features
from organisations.models import Organisation, OrganisationMember
from progression.models import MemberProgression, ProgressionStage, ProgressionSystem

ALL_ON = {key: True for key in FEATURES_BY_KEY}


class EffectiveFeaturesTests(TestCase):
    @override_settings(STRIPE_PUBLIC_KEY='', STRIPE_SECRET_KEY='')
    def test_defaults_for_a_new_club(self):
        self.assertEqual(
            effective_features({}),
            {'billing', 'documents', 'applications', 'announcements', 'member_portal', 'waiting_lists'},
        )

    @override_settings(STRIPE_PUBLIC_KEY='pk_test', STRIPE_SECRET_KEY='sk_test')
    def test_online_payments_default_on_only_when_stripe_is_configured(self):
        self.assertIn('online_payments', effective_features({}))

    def test_a_feature_is_off_while_anything_it_needs_is_off(self):
        self.assertNotIn('inventory', effective_features({'inventory': True, 'billing': False}))
        self.assertNotIn('online_payments', effective_features({**ALL_ON, 'member_portal': False}))
        self.assertIn('inventory', effective_features({'inventory': True, 'billing': True}))

    def test_unknown_feature_is_an_error(self):
        org = Organisation(name='X', slug='x')
        with self.assertRaises(ValueError):
            org.has_feature('time_travel')


class FeatureTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON))
        cls.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')
        cls.member = Member.objects.create(organisation=cls.org, name='Alice A', email='alice@example.com')
        cls.cls = Class.objects.create(organisation=cls.org, name='Juniors', max_capacity=1)
        cls.invoice = Invoice.objects.create(
            organisation=cls.org, member=cls.member, amount=30, period='October',
            due_date=date.today() - timedelta(days=10),
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def switch_off(self, *keys):
        self.org.set_features({**ALL_ON, **{k: False for k in keys}})
        self.org.save(update_fields=['features'])

    def url(self, name, **kwargs):
        return reverse(name, kwargs={'org_slug': self.org.slug, **kwargs})


class SwitchedOffRoutesTests(FeatureTestBase):
    """Each feature's routes 404 when it is off and still work when it is on."""

    def routes(self):
        token = self.member.token
        return {
            'gradings': [('get', self.url('grading_list'))],
            'grades': [('get', self.url('progression_settings')),
                       ('get', self.url('progression_import'))],
            'billing': [('get', self.url('invoice_list')),
                        ('get', self.url('org_finance')),
                        ('post', self.url('member_billing_policy_set', pk=self.member.pk))],
            'inventory': [('get', self.url('inventory_list'))],
            'documents': [('get', self.url('waiver_list'))],
            'applications': [('get', self.url('application_list')),
                             ('get', reverse('member_signup', kwargs={'org_slug': self.org.slug}))],
            'announcements': [('get', self.url('org_announcements'))],
            'member_portal': [('get', reverse('member_portal', kwargs={'token': token})),
                              ('post', self.url('member_send_welcome', pk=self.member.pk))],
            'online_payments': [('post', reverse('portal_checkout', kwargs={'token': token, 'invoice_pk': self.invoice.pk}))],
            'waiting_lists': [('post', self.url('class_waiting_remove', pk=self.cls.pk, member_pk=self.member.pk))],
        }

    def test_every_feature_has_routes_here(self):
        self.assertEqual(set(self.routes()), {f.key for f in FEATURES})

    def test_routes_404_when_switched_off(self):
        for key, routes in self.routes().items():
            self.switch_off(key)
            for method, route in routes:
                with self.subTest(feature=key, route=route):
                    self.assertEqual(getattr(self.client, method)(route).status_code, 404)

    def test_routes_work_when_switched_on(self):
        for key, routes in self.routes().items():
            for method, route in routes:
                if key == 'online_payments':
                    continue  # needs Stripe keys; covered by the 404 case only
                with self.subTest(feature=key, route=route):
                    self.assertNotEqual(getattr(self.client, method)(route).status_code, 404)

    def test_bulk_invoice_actions_404_when_billing_off(self):
        self.switch_off('billing')
        for action in ('invoice', 'invoice_create'):
            response = self.client.post(self.url('member_bulk_action'), {
                'bulk_action': action, 'member_ids': [self.member.pk],
                'period': 'Nov', 'amount': '10', 'due_date': '2026-11-30',
            })
            self.assertEqual(response.status_code, 404)
        self.assertEqual(Invoice.objects.count(), 1)


class SwitchedOffPagesTests(FeatureTestBase):
    def test_sidebar_hides_switched_off_features(self):
        self.switch_off('billing', 'grades', 'documents', 'applications', 'announcements', 'inventory')
        response = self.client.get(self.url('org_dashboard'))
        for name in ('invoice_list', 'billing_policies', 'org_finance', 'progression_settings',
                     'waiver_list', 'application_list', 'org_announcements', 'inventory_list'):
            with self.subTest(link=name):
                self.assertNotContains(response, self.url(name))

    def test_member_page_hides_switched_off_sections(self):
        self.switch_off('billing', 'grades', 'documents', 'member_portal')
        response = self.client.get(self.url('member_detail', pk=self.member.pk))
        self.assertEqual(response.status_code, 200)
        for name in ('member_promote', 'member_billing_policy_set', 'member_send_welcome'):
            self.assertNotContains(response, self.url(name, pk=self.member.pk))
        self.assertNotContains(response, 'Documents</h6>')

    def test_member_form_drops_billing_fields(self):
        self.switch_off('billing')
        response = self.client.get(self.url('member_add'))
        self.assertNotIn('monthly_fee', response.context['form'].fields)
        self.assertNotIn('billing_policy', response.context['form'].fields)

    def test_portal_hides_billing_and_grades(self):
        self.switch_off('billing', 'grades')
        response = self.client.get(reverse('member_portal', kwargs={'token': self.member.token}))
        self.assertNotContains(response, 'Outstanding invoices')
        self.assertNotContains(response, 'Current grade')


class SwitchedOffBehaviourTests(FeatureTestBase):
    def test_capacity_ignored_without_waiting_lists(self):
        other = Member.objects.create(organisation=self.org, name='Bob B')
        ClassMember.objects.create(assigned_class=self.cls, member=other)
        self.assertTrue(self.cls.is_full)

        self.switch_off('waiting_lists')
        self.cls.organisation = Organisation.objects.get(pk=self.org.pk)
        self.assertFalse(self.cls.is_full)
        self.client.post(self.url('class_enrol', pk=self.cls.pk), {'member_id': self.member.pk})
        self.assertTrue(ClassMember.objects.filter(assigned_class=self.cls, member=self.member).exists())
        self.assertFalse(WaitingList.objects.exists())

    def test_new_members_not_auto_graded_without_grades(self):
        system = ProgressionSystem.objects.create(organisation=self.org, name='Kyu', assign_to_new_members=True)
        ProgressionStage.objects.create(system=system, name='White', is_default=True)
        self.switch_off('grades')
        self.client.post(self.url('member_add'), {
            'name': 'Cara C', 'is_active': 'on',
            'guardians-TOTAL_FORMS': '0', 'guardians-INITIAL_FORMS': '0',
        })
        self.assertTrue(Member.objects.filter(name='Cara C').exists())
        self.assertFalse(MemberProgression.objects.exists())

    def test_invoice_email_has_no_portal_link_without_portal(self):
        self.switch_off('member_portal')
        self.invoice.member.organisation = Organisation.objects.get(pk=self.org.pk)
        send_invoice_email(self.invoice)
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn(self.member.token, mail.outbox[0].body)
        self.assertNotIn(self.member.token, mail.outbox[0].alternatives[0][0])

    def test_overdue_reminders_skip_clubs_without_billing(self):
        self.switch_off('billing')
        call_command('send_overdue_reminders', stdout=StringIO())
        self.assertEqual(mail.outbox, [])

    def test_overdue_reminders_still_sent_with_billing_on(self):
        call_command('send_overdue_reminders', stdout=StringIO())
        self.assertEqual([m.to for m in mail.outbox], [['alice@example.com']])

    def test_stale_tokens_rotated_but_not_emailed_without_portal(self):
        self.switch_off('member_portal')
        Member.objects.filter(pk=self.member.pk).update(token_created_at=timezone.now() - timedelta(days=400))
        old_token = self.member.token
        call_command('rotate_stale_portal_tokens', stdout=StringIO())
        self.member.refresh_from_db()
        self.assertNotEqual(self.member.token, old_token)
        self.assertEqual(mail.outbox, [])


class FeatureSettingsTests(FeatureTestBase):
    def save(self, features):
        return self.client.post(self.url('org_settings'), {'action': 'features', 'features': features})

    def test_admin_can_switch_features(self):
        self.save(['billing', 'grades'])
        self.org.refresh_from_db()
        self.assertEqual(self.org.enabled_features, {'billing', 'grades'})
        self.assertEqual(self.client.get(self.url('waiver_list')).status_code, 404)

    def test_switching_back_on_keeps_data(self):
        self.save([])
        self.save([f.key for f in FEATURES])
        response = self.client.get(self.url('invoice_list'))
        self.assertEqual(list(response.context['invoices']), [self.invoice])

    def test_settings_page_lists_every_feature(self):
        response = self.client.get(self.url('org_settings'))
        for feature in FEATURES:
            self.assertContains(response, f'value="{feature.key}"')

    def test_coach_cannot_change_features(self):
        coach = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=coach, organisation=self.org, role='coach')
        self.client.force_login(coach)
        self.assertEqual(self.save([]).status_code, 403)
        self.org.refresh_from_db()
        self.assertEqual(self.org.features, ALL_ON)

    def test_other_org_admin_cannot_change_features(self):
        other = Organisation.objects.create(name='Other', slug='other')
        admin_b = User.objects.create_user('admin-b', password='pw')
        OrganisationMember.objects.create(user=admin_b, organisation=other, role='org_admin')
        self.client.force_login(admin_b)
        self.assertEqual(self.save([]).status_code, 403)
        self.org.refresh_from_db()
        self.assertEqual(self.org.features, ALL_ON)

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        response = self.save([])
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])
        self.org.refresh_from_db()
        self.assertEqual(self.org.features, ALL_ON)


class SetupWizardTests(TestCase):
    def test_setup_stores_chosen_features(self):
        response = self.client.post(reverse('setup'), {
            'org_name': 'New Club', 'username': 'boss',
            'password': 'a-long-password', 'password_confirm': 'a-long-password',
            'features': ['grades', 'billing', 'member_portal'],
        })
        self.assertEqual(response.status_code, 302)
        org = Organisation.objects.get(name='New Club')
        self.assertEqual(org.enabled_features, {'grades', 'billing', 'member_portal'})

    @override_settings(STRIPE_PUBLIC_KEY='', STRIPE_SECRET_KEY='')
    def test_setup_form_preticks_defaults(self):
        response = self.client.get(reverse('setup'))
        ticked = {row['key'] for row in response.context['feature_rows'] if row['enabled']}
        self.assertEqual(ticked, effective_features({}))


class UpgradeBackfillTests(TestCase):
    """Clubs that existed before feature switches keep everything switched on."""

    def test_backfill_switches_on_every_feature_that_existed(self):
        from importlib import import_module

        from django.apps import apps
        migration = import_module('organisations.migrations.0009_organisation_features')
        org = Organisation.objects.create(name='Existing Club', slug='existing', features={})

        migration.switch_on_existing_features(apps, None)

        org.refresh_from_db()
        self.assertEqual(org.enabled_features, set(FEATURES_BY_KEY) - {'gradings'})

    def test_backfill_covers_every_feature_that_existed(self):
        from importlib import import_module
        migration = import_module('organisations.migrations.0009_organisation_features')
        # A feature added later must not be switched on for existing clubs by this
        # migration; it gets its own default. This pins the list at introduction.
        added_since = {'gradings'}
        self.assertEqual(set(migration.EXISTING_FEATURES), set(FEATURES_BY_KEY) - added_since)
