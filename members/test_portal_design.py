from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from classes.models import Class, ClassMember, Session, SessionAbsence, SessionBooking
from members.models import Member
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation


class PortalDesignTests(TestCase):
    """The redesigned member portal: Up next on the Overview, the section navigation, and club colours."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(
            name='Test Club', slug='test-club', features={key: True for key in FEATURES_BY_KEY},
            settings={'sidebar_color': '#FACC15', 'accent_color': '#1E3A5F'},
        )
        cls.member = Member.objects.create(organisation=cls.org, name='Alice Able', email='alice@example.com')
        today = timezone.localdate()
        cls.juniors = Class.objects.create(organisation=cls.org, name='Juniors', is_active=True)
        cls.seniors = Class.objects.create(organisation=cls.org, name='Seniors', is_active=True)
        cls.soon = Session.objects.create(assigned_class=cls.juniors, date=today + timedelta(days=2))
        cls.later = Session.objects.create(assigned_class=cls.seniors, date=today + timedelta(days=5))
        cls.skipped = Session.objects.create(assigned_class=cls.juniors, date=today + timedelta(days=3))
        cls.far = Session.objects.create(assigned_class=cls.juniors, date=today + timedelta(days=40))

    def page(self, name='member_portal'):
        return self.client.get(reverse(name, kwargs={'token': self.member.token})).content.decode()

    def test_up_next_lists_bookings_and_enrolled_sessions_soonest_first(self):
        ClassMember.objects.create(member=self.member, assigned_class=self.juniors, auto_enrol=True)
        SessionBooking.objects.create(member=self.member, session=self.later)
        SessionAbsence.objects.create(member=self.member, session=self.skipped)
        html = self.page()
        self.assertIn('Up next', html)
        up_next = html[html.index('Up next'):html.index('Summary')]
        self.assertLess(up_next.index('Juniors'), up_next.index('Seniors'))
        self.assertIn('Enrolled', up_next)
        self.assertIn('Booked', up_next)
        # A session they've said they can't make, and one more than three weeks away, aren't listed.
        self.assertEqual(up_next.count('Juniors'), 1)

    def test_up_next_invites_booking_when_nothing_is_booked(self):
        html = self.page()
        self.assertIn('Nothing booked yet', html)

    def test_sections_follow_the_clubs_features(self):
        html = self.page()
        for label in ('Overview', 'Book', 'Syllabus', 'Payments', 'Plan', 'My details', 'Code of Conduct'):
            self.assertIn(f'></i> {label}', html)
        self.org.features = {key: False for key in FEATURES_BY_KEY} | {'member_portal': True}
        self.org.save()
        Class.objects.update(is_active=False)
        html = self.page()
        self.assertNotIn(reverse('portal_payments', kwargs={'token': self.member.token}) + '"', html)
        self.assertNotIn(reverse('portal_syllabus', kwargs={'token': self.member.token}) + '"', html)
        self.assertNotIn(reverse('portal_bookings', kwargs={'token': self.member.token}) + '"', html)

    def test_current_section_is_marked_and_titled(self):
        html = self.page('portal_payments')
        self.assertIn('<h1 class="p-title">Payments</h1>', html)
        self.assertIn('aria-current="page"', html)
        self.assertIn('bi-credit-card-fill', html)

    def test_club_colours_with_readable_text(self):
        html = self.page()
        self.assertIn('--p-brand: #FACC15;', html)
        self.assertIn('--p-on-brand: #000000;', html)   # yellow header: black text
        self.assertIn('--p-accent: #1E3A5F;', html)
        self.assertIn('--p-on-accent: #ffffff;', html)  # navy buttons: white text

    def test_header_is_the_sidebar_colour_and_the_menu_row_the_button_colour(self):
        html = self.page()
        self.assertNotIn('--p-brand-end', html)        # a single solid header colour, no gradient
        css = open('static/css/portal.css').read()
        self.assertIn('.p-hero {\n    background: var(--p-brand);', css)
        self.assertIn('background: var(--p-accent); color: var(--p-on-accent);', css)

    def test_light_by_default_with_a_dark_mode_switch(self):
        html = self.page()
        self.assertIn('data-bs-theme="light"', html)
        self.assertNotIn('prefers-color-scheme', html)  # the member chooses, not the device
        self.assertEqual(html.count('data-p-theme-toggle'), 2)  # moon button (desktop) and switch (More sheet)
        self.assertIn("localStorage.getItem('portalTheme') === 'dark'", html)
        login = self.client.get(reverse('portal_login_request', kwargs={'org_slug': self.org.slug})).content.decode()
        self.assertIn("localStorage.getItem('portalTheme') === 'dark'", login)
