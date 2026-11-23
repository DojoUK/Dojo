import json
from datetime import date, timedelta
from decimal import Decimal

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import Invoice, Payment
from classes.models import Attendance, Class, ClassCoach, ClassMember, Session
from members.models import Member
from organisations import dashboard
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember

ALL_ON = {key: True for key in FEATURES_BY_KEY}
HAS_NETWORK = apps.is_installed('network')      # the Dojo network isn't on every branch


class DashboardTestBase(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON),
                                               settings={'two_factor': 'optional', 'onboarding_dismissed': True})
        self.admin = User.objects.create_user('admin', password='pw')
        self.admin_om = OrganisationMember.objects.create(user=self.admin, organisation=self.org, role='org_admin')
        self.coach = User.objects.create_user('coach', password='pw')
        self.coach_om = OrganisationMember.objects.create(user=self.coach, organisation=self.org, role='coach')
        self.cls = Class.objects.create(organisation=self.org, name='Adults')
        self.other_cls = Class.objects.create(organisation=self.org, name='Juniors')
        ClassCoach.objects.create(assigned_class=self.cls, user=self.coach)
        self.today = timezone.localdate()
        self.client.force_login(self.admin)

    def url(self, name, **kwargs):
        return reverse(name, kwargs={'org_slug': self.org.slug, **kwargs})

    def board(self, user=None, membership=None):
        return dashboard.Board(self.org, user or self.admin, membership or self.admin_om)

    def keys(self, layout):
        return [item['key'] for item in layout]

    def save(self, widgets):
        return self.client.post(self.url('org_dashboard_layout'), json.dumps({'widgets': widgets}),
                                content_type='application/json')

    def switch_off(self, *features):
        self.org.features = {**self.org.features, **{f: False for f in features}}
        self.org.save()
        self.org = Organisation.objects.get(pk=self.org.pk)     # the feature list is cached on the object


class LayoutTests(DashboardTestBase):
    def test_default_layout_is_the_old_dashboard(self):
        layout = dashboard.layout_for(self.board())
        self.assertEqual(self.keys(layout), list(dashboard.DEFAULT_WIDGETS))
        tiles = [item for item in layout if item['y'] == 0]
        self.assertEqual([(t['x'], t['w'], t['h']) for t in tiles], [(0, 6, 3), (6, 6, 3), (12, 6, 3), (18, 6, 3)])
        self.assertEqual(layout[4], {'key': 'needs_attention', 'x': 0, 'y': 3, 'w': 24, 'h': 5})
        self.assertEqual(layout[5:], [
            {'key': 'upcoming_sessions', 'x': 0, 'y': 8, 'w': 16, 'h': 8},
            {'key': 'quick_actions', 'x': 16, 'y': 8, 'w': 8, 'h': 8},
        ])
        # Every row of the default layout fills the 24 columns.
        for y in {item['y'] for item in layout}:
            self.assertEqual(sum(i['w'] for i in layout if i['y'] == y), dashboard.COLUMNS)

    def test_default_layout_closes_gaps_for_switched_off_features(self):
        self.switch_off('billing', 'inventory')
        layout = dashboard.layout_for(self.board())
        self.assertNotIn('revenue_month', self.keys(layout))
        at_risk = next(item for item in layout if item['key'] == 'at_risk')
        self.assertEqual((at_risk['x'], at_risk['y']), (12, 0))

    def test_coach_sees_their_widgets_moved_up(self):
        self.org.settings['dashboard'] = {'version': 2, 'widgets': [
            {'key': 'needs_attention', 'x': 0, 'y': 0, 'w': 24, 'h': 4},
            {'key': 'upcoming_sessions', 'x': 0, 'y': 4, 'w': 16, 'h': 8},
            {'key': 'quick_actions', 'x': 16, 'y': 4, 'w': 8, 'h': 8},
        ]}
        self.org.save()
        layout = dashboard.layout_for(self.board(self.coach, self.coach_om))
        self.assertEqual(layout, [
            {'key': 'upcoming_sessions', 'x': 0, 'y': 0, 'w': 16, 'h': 8},
            {'key': 'quick_actions', 'x': 16, 'y': 0, 'w': 8, 'h': 8},
        ])
        # Admins see the layout exactly as it was saved.
        self.assertEqual(dashboard.layout_for(self.board())[1]['y'], 4)

    def test_capability_widget_follows_the_permission(self):
        widget = dashboard.WIDGETS_BY_KEY['upcoming_gradings']
        self.assertFalse(widget.visible(self.board(self.coach, self.coach_om)))
        self.coach_om.can_manage_gradings = True
        self.coach_om.save()
        self.assertTrue(widget.visible(self.board(self.coach, self.coach_om)))
        self.switch_off('gradings')
        self.assertFalse(widget.visible(self.board(self.coach, self.coach_om)))

    def test_clean_drops_unknown_and_repeated_widgets_and_fixes_sizes(self):
        layout = dashboard.clean([
            {'key': 'no_such_widget', 'x': 0, 'y': 0, 'w': 5, 'h': 3},
            {'key': 'active_members', 'x': -3, 'y': -1, 'w': 1, 'h': 99},
            {'key': 'active_members', 'x': 9, 'y': 9, 'w': 5, 'h': 3},
            {'key': 'quick_actions', 'x': 'left', 'y': 0, 'w': 5, 'h': 3},
            {'key': 'upcoming_sessions', 'x': 200, 'y': 0, 'w': 14, 'h': 8},
            'rubbish',
        ], self.board())
        self.assertEqual(layout, [
            {'key': 'active_members', 'x': 0, 'y': 0, 'w': 3, 'h': 6},
            {'key': 'upcoming_sessions', 'x': dashboard.COLUMNS - 14, 'y': 0, 'w': 14, 'h': 8},
        ])

    def test_overlapping_widgets_are_pushed_down(self):
        layout = dashboard.clean([
            {'key': 'active_members', 'x': 0, 'y': 0, 'w': 5, 'h': 3},
            {'key': 'sessions_week', 'x': 2, 'y': 1, 'w': 5, 'h': 3},
            {'key': 'at_risk', 'x': 4, 'y': 3, 'w': 5, 'h': 3},
        ], self.board())
        self.assertEqual([(i['key'], i['y']) for i in layout],
                         [('active_members', 0), ('sessions_week', 3), ('at_risk', 6)])
        for a in layout:
            for b in layout:
                if a is not b:
                    self.assertFalse(dashboard.overlaps(a, b))

    def test_old_fixed_cell_layouts_are_stretched_onto_the_columns(self):
        # Saved before the grid stretched: 50px cells, 20 across on a laptop.
        self.org.settings['dashboard'] = {'version': 1, 'widgets': [
            {'key': 'active_members', 'x': 0, 'y': 0, 'w': 5, 'h': 3},
            {'key': 'sessions_week', 'x': 5, 'y': 0, 'w': 5, 'h': 3},
            {'key': 'needs_attention', 'x': 0, 'y': 3, 'w': 20, 'h': 5},
            {'key': 'upcoming_sessions', 'x': 0, 'y': 8, 'w': 14, 'h': 8},
            {'key': 'quick_actions', 'x': 14, 'y': 8, 'w': 6, 'h': 8},
        ]}
        self.org.save()
        layout = {i['key']: (i['x'], i['y'], i['w'], i['h']) for i in dashboard.layout_for(self.board())}
        self.assertEqual(layout, {
            'active_members': (0, 0, 6, 3), 'sessions_week': (6, 0, 6, 3),
            'needs_attention': (0, 3, 24, 5),
            'upcoming_sessions': (0, 8, 17, 8), 'quick_actions': (17, 8, 7, 8),
        })

    def test_old_layouts_wider_than_a_laptop_keep_their_proportions(self):
        self.org.settings['dashboard'] = {'widgets': [      # no version: the first format
            {'key': 'attendance_trend', 'x': 0, 'y': 0, 'w': 16, 'h': 7},
            {'key': 'quick_actions', 'x': 16, 'y': 0, 'w': 16, 'h': 7},
        ]}
        self.org.save()
        layout = dashboard.layout_for(self.board())
        self.assertEqual([(i['x'], i['w']) for i in layout], [(0, 12), (12, 12)])

    def test_saving_writes_the_current_version(self):
        self.save([{'key': 'quick_actions', 'x': 0, 'y': 0, 'w': 8, 'h': 8}])
        self.org.refresh_from_db()
        self.assertEqual(self.org.settings['dashboard']['version'], dashboard.LAYOUT_VERSION)
        self.assertEqual(dashboard.layout_for(self.board()), [{'key': 'quick_actions', 'x': 0, 'y': 0, 'w': 8, 'h': 8}])

    def test_every_widget_fits_the_grid_and_says_how_narrow_it_can_go(self):
        for widget in dashboard.WIDGETS:
            with self.subTest(widget=widget.key):
                self.assertLessEqual(widget.min_size[0], widget.size[0])
                self.assertLessEqual(widget.size[0], min(widget.max_size[0], dashboard.COLUMNS))
                self.assertGreaterEqual(widget.min_px, 150)
                self.assertEqual(widget.catalogue_entry()['minPx'], widget.min_px)

    def test_first_fit_and_compact(self):
        placed = [{'x': 0, 'y': 0, 'w': 5, 'h': 3}, {'x': 5, 'y': 0, 'w': 5, 'h': 3}]
        self.assertEqual(dashboard.first_fit(placed, 5, 3, 12), (0, 3))
        self.assertEqual(dashboard.first_fit(placed, 2, 2, 12), (10, 0))
        floating = [{'x': 0, 'y': 5, 'w': 4, 'h': 2}, {'x': 0, 'y': 9, 'w': 4, 'h': 2}]
        self.assertEqual([i['y'] for i in dashboard.compact(floating)], [0, 2])


class SavingTests(DashboardTestBase):
    def test_admin_saves_the_clubs_layout(self):
        self.org.settings['sidebar_color'] = '#123456'
        self.org.save()
        response = self.save([
            {'key': 'quick_actions', 'x': 0, 'y': 0, 'w': 6, 'h': 8},
            {'key': 'attendance_trend', 'x': 6, 'y': 0, 'w': 10, 'h': 7},
        ])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.keys(response.json()['widgets']), ['quick_actions', 'attendance_trend'])
        self.org.refresh_from_db()
        self.assertEqual(self.keys(self.org.settings['dashboard']['widgets']), ['quick_actions', 'attendance_trend'])
        self.assertEqual(self.org.settings['sidebar_color'], '#123456')

        page = self.client.get(self.url('org_dashboard'))
        self.assertEqual([w['key'] for w in page.context['widgets']], ['quick_actions', 'attendance_trend'])
        self.assertContains(page, 'data-key="attendance_trend"')
        self.assertNotContains(page, 'data-key="active_members"')
        self.assertNotIn('member_count', page.context)      # widgets not on the layout aren't worked out

    def test_an_empty_layout_is_kept(self):
        self.save([])
        page = self.client.get(self.url('org_dashboard'))
        self.assertEqual(page.context['widgets'], [])
        self.assertContains(page, 'Your dashboard is empty.')

    def test_reset_brings_back_the_default(self):
        self.save([{'key': 'quick_actions', 'x': 0, 'y': 0, 'w': 6, 'h': 8}])
        response = self.client.post(self.url('org_dashboard_layout'), json.dumps({'reset': True}),
                                    content_type='application/json')
        self.assertEqual(self.keys(response.json()['widgets']), list(dashboard.DEFAULT_WIDGETS))
        self.org.refresh_from_db()
        self.assertNotIn('dashboard', self.org.settings)

    def test_bad_bodies_are_refused(self):
        for body in ('not json', '[]', '{"widgets": "all"}'):
            with self.subTest(body=body):
                response = self.client.post(self.url('org_dashboard_layout'), body, content_type='application/json')
                self.assertEqual(response.status_code, 400)

    def test_widgets_the_club_cant_use_are_not_saved(self):
        self.switch_off('billing', 'inventory')
        response = self.save([{'key': 'revenue_trend', 'x': 0, 'y': 0, 'w': 10, 'h': 7}])
        self.assertEqual(response.json()['widgets'], [])

    def test_coaches_cant_customise(self):
        self.client.force_login(self.coach)
        self.assertEqual(self.save([]).status_code, 403)
        self.assertEqual(self.client.get(self.url('org_dashboard_widget', key='quick_actions')).status_code, 403)
        page = self.client.get(self.url('org_dashboard'))
        self.assertNotContains(page, 'id="dashCustomise"')
        self.assertNotContains(page, 'dash-remove')
        self.org.refresh_from_db()
        self.assertNotIn('dashboard', self.org.settings)

    def test_other_clubs_admins_cant_customise(self):
        other = Organisation.objects.create(name='Other', slug='other')
        admin_b = User.objects.create_user('admin-b', password='pw')
        OrganisationMember.objects.create(user=admin_b, organisation=other, role='org_admin')
        self.client.force_login(admin_b)
        self.assertEqual(self.save([]).status_code, 403)

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        response = self.save([])
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])


class WidgetTests(DashboardTestBase):
    """Every widget renders, with and without data, on the page and on its own."""

    def add_data(self):
        old = self.today - timedelta(days=90)
        self.ann = Member.objects.create(organisation=self.org, name='Ann Able', joined_date=old,
                                         date_of_birth=date(2000, (self.today + timedelta(days=1)).month,
                                                            (self.today + timedelta(days=1)).day))
        self.bob = Member.objects.create(organisation=self.org, name='Bob Burns', joined_date=old)
        self.cat = Member.objects.create(organisation=self.org, name='Cat Cole', joined_date=self.today)
        for m in (self.ann, self.bob):
            ClassMember.objects.create(assigned_class=self.cls, member=m)
        for days_ago in (3, 10, 17, 40):
            session = Session.objects.create(assigned_class=self.cls, date=self.today - timedelta(days=days_ago))
            Attendance.objects.create(session=session, member=self.ann, present=True)
            Attendance.objects.create(session=session, member=self.bob, present=days_ago == 40)
        Session.objects.create(assigned_class=self.cls, date=self.today + timedelta(days=2))
        Session.objects.create(assigned_class=self.other_cls, date=self.today + timedelta(days=3))
        invoice = Invoice.objects.create(organisation=self.org, member=self.ann, amount=Decimal('40'),
                                         period='This month', due_date=self.today - timedelta(days=5))
        Payment.objects.create(invoice=invoice, amount=Decimal('25'), paid_at=timezone.now())
        Invoice.objects.create(organisation=self.org, member=self.bob, amount=Decimal('30'),
                               period='This month', due_date=self.today - timedelta(days=5))
        if HAS_NETWORK:
            from network import accounts
            from network.models import ClubReview
            reviewer = accounts.create_account('Jo Bloggs', 'jo@example.com', 'a-long-passphrase-1')
            ClubReview.objects.create(account=reviewer, organisation=self.org, rating=4, text='Friendly club')
        from progression.models import Grading, GradingSession, ProgressionSystem
        system = ProgressionSystem.objects.create(organisation=self.org, name='Kyu', uses_gradings=True)
        grading = Grading.objects.create(organisation=self.org, name='Winter grading', system=system)
        GradingSession.objects.create(grading=grading, date=self.today + timedelta(days=20))

    def all_widgets(self):
        placed = []
        for widget in self.available():
            x, y = dashboard.first_fit(placed, *widget.size, 24)
            placed.append({'key': widget.key, 'x': x, 'y': y, 'w': widget.size[0], 'h': widget.size[1]})
        return placed

    def available(self):
        return [w for w in dashboard.WIDGETS if w.available(self.org)]

    def test_every_widget_renders_without_data(self):
        self.save(self.all_widgets())
        page = self.client.get(self.url('org_dashboard'))
        self.assertEqual(len(page.context['widgets']), len(self.available()))
        self.assertContains(page, 'No sessions in the next 7 days.')
        if HAS_NETWORK:
            self.assertContains(page, 'No reviews yet.')

    def test_every_widget_renders_with_data(self):
        self.add_data()
        self.save(self.all_widgets())
        page = self.client.get(self.url('org_dashboard'))
        context = page.context
        self.assertEqual(context['member_count'], 3)
        self.assertEqual(context['sessions_this_week'], 2)
        self.assertEqual(context['revenue_month'], Decimal('25'))
        self.assertEqual(context['overdue_count'], 2)
        self.assertEqual(context['joined_this_month'], 1)
        if HAS_NETWORK:
            self.assertEqual(context['rating']['average'], 4)
            self.assertEqual(context['rating_stars'], ['bi-star-fill'] * 4 + ['bi-star'])
            self.assertContains(page, 'Friendly club')
        self.assertEqual([r['member'] for r in context['birthdays']], [self.ann])
        self.assertEqual(context['class_rows'][0]['average'], 1.0)
        self.assertEqual(len(context['grading_sessions']), 1)
        self.assertEqual(len(context['payments']), 1)
        self.assertEqual(sum(json.loads(context['attendance_chart'])['series'][0]['data']), 5)
        self.assertEqual(json.loads(context['revenue_chart'])['series'][0]['data'][-1], 25.0)
        self.assertContains(page, 'Winter grading')
        self.assertContains(page, 'Bob Burns')               # not seen lately

    def test_coach_numbers_cover_their_own_classes(self):
        self.add_data()
        self.client.force_login(self.coach)
        page = self.client.get(self.url('org_dashboard'))
        # The default layout less the admin widgets, moved up into the space they leave.
        self.assertEqual([(w['key'], w['x'], w['y']) for w in page.context['widgets']], [
            ('active_members', 0, 0), ('sessions_week', 6, 0), ('quick_actions', 16, 0), ('upcoming_sessions', 0, 3),
        ])
        self.assertEqual(page.context['sessions_this_week'], 1)   # not the Juniors session
        self.assertContains(page, '(your classes)')

    def test_widget_endpoint_renders_one_widget_in_place(self):
        self.add_data()
        response = self.client.get(self.url('org_dashboard_widget', key='attendance_trend') + '?x=3&y=12&w=2&h=99')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-key="attendance_trend"')
        self.assertContains(response, 'data-x="3" data-y="12" data-w="6" data-h="24"')   # clamped to its limits
        self.assertContains(response, 'data-min-px="320"')
        self.assertContains(response, 'grid-area: 13 / 4 / span 24 / span 6')
        self.assertContains(response, 'data-chart=')

    def test_widget_endpoint_refuses_unknown_and_switched_off_widgets(self):
        self.assertEqual(self.client.get(self.url('org_dashboard_widget', key='nope')).status_code, 404)
        self.switch_off('billing', 'inventory')
        self.assertEqual(self.client.get(self.url('org_dashboard_widget', key='revenue_trend')).status_code, 404)

    def test_catalogue_lists_what_the_club_can_add(self):
        page = self.client.get(self.url('org_dashboard'))
        keys = [entry['key'] for entry in page.context['dash_config']['catalogue']]
        self.assertEqual(keys, [w.key for w in self.available()])
        self.switch_off('billing', 'inventory', 'gradings', 'applications')
        page = self.client.get(self.url('org_dashboard'))
        keys = {entry['key'] for entry in page.context['dash_config']['catalogue']}
        self.assertFalse(keys & {'revenue_month', 'revenue_trend', 'recent_payments', 'outstanding_invoices',
                                 'upcoming_gradings', 'applications_list'})

    def test_needs_attention_all_clear(self):
        self.switch_off('billing', 'inventory')        # no single session price to set
        self.org.latitude, self.org.longitude, self.org.address_line1 = 51.48, -3.18, '1 Dojo Way'
        self.org.save()
        data = dashboard.needs_attention(self.board())
        self.assertTrue(data['all_clear'], data)


class OptionalPartsTests(DashboardTestBase):
    def test_network_widgets_need_the_network_app(self):
        for key in ('club_rating', 'recent_reviews'):
            self.assertEqual(dashboard.WIDGETS_BY_KEY[key].available(self.org), HAS_NETWORK)

    def test_stars_round_to_the_nearest_half(self):
        self.assertEqual(dashboard.star_icons(3.7), ['bi-star-fill'] * 3 + ['bi-star-half', 'bi-star'])
        self.assertEqual(dashboard.star_icons(None), ['bi-star'] * 5)

    def test_not_seen_groups_without_the_report(self):
        today = self.today
        old = today - timedelta(days=60)
        gone = Member.objects.create(organisation=self.org, name='Gary Gone', joined_date=old)
        Member.objects.create(organisation=self.org, name='Rita Regular', joined_date=old)
        never = Member.objects.create(organisation=self.org, name='Nina Never', joined_date=old)
        Member.objects.create(organisation=self.org, name='Newt New', joined_date=today)
        for name, days_ago in (('Gary Gone', 30), ('Rita Regular', 3)):
            session = Session.objects.create(assigned_class=self.cls, date=today - timedelta(days=days_ago))
            Attendance.objects.create(session=session, member=Member.objects.get(name=name), present=True)
        groups = dashboard._not_seen_here(self.board())
        self.assertEqual([r['member'] for r in groups['not_seen']], [gone])
        self.assertEqual(groups['not_seen'][0]['days_since'], 30)
        self.assertEqual([r['member'] for r in groups['never']], [never])


class BirthdayTests(DashboardTestBase):
    def test_leap_day_birthdays_fall_on_1_march_in_other_years(self):
        member = Member.objects.create(organisation=self.org, name='Leap Lee', date_of_birth=date(2004, 2, 29))
        board = dashboard.Board(self.org, self.admin, self.admin_om, today=date(2027, 2, 20))
        rows = dashboard.birthdays(board)['birthdays']
        self.assertEqual([(r['member'], r['day'], r['age']) for r in rows], [(member, date(2027, 3, 1), 23)])

    def test_birthdays_wrap_into_next_year(self):
        Member.objects.create(organisation=self.org, name='New Year', date_of_birth=date(1990, 1, 3))
        Member.objects.create(organisation=self.org, name='Far Away', date_of_birth=date(1990, 6, 3))
        board = dashboard.Board(self.org, self.admin, self.admin_om, today=date(2026, 12, 28))
        rows = dashboard.birthdays(board)['birthdays']
        self.assertEqual([(r['member'].name, r['days']) for r in rows], [('New Year', 6)])
