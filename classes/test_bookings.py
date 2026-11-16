from datetime import timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from classes import booking
from classes.models import Class, ClassMember, Session, SessionAbsence, SessionBooking
from classes.register import register_enrolments
from members.models import Member
from progression.models import MemberProgression, ProgressionStage, ProgressionSystem
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember

ALL_ON = {key: True for key in FEATURES_BY_KEY}


class BookingTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON))
        cls.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')
        cls.member = Member.objects.create(organisation=cls.org, name='Alice Able', email='alice@example.com')
        cls.other = Member.objects.create(organisation=cls.org, name='Bob Best', email='bob@example.com')
        cls.juniors = Class.objects.create(
            organisation=cls.org, name='Juniors', description='Fun judo for 7 to 11 year olds.', is_active=True,
        )
        # A session next week, with no schedule time: starts at midnight, so well clear of the cut-off.
        cls.session = Session.objects.create(assigned_class=cls.juniors, date=timezone.localdate() + timedelta(days=7))

    def portal(self, name, **kwargs):
        return reverse(name, kwargs={'token': self.member.token, **kwargs})

    def act(self, **data):
        return self.client.post(self.portal('portal_booking_action'), data)


class EnrolmentTests(BookingTestBase):
    def test_enrol_creates_a_whole_class_enrolment_and_emails(self):
        booking.enrol(self.member, self.juniors)
        self.assertTrue(ClassMember.objects.get(member=self.member, assigned_class=self.juniors).auto_enrol)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['alice@example.com'])
        self.assertIn('Juniors', mail.outbox[0].subject)

    def test_cannot_enrol_twice_or_in_an_inactive_class(self):
        booking.enrol(self.member, self.juniors)
        with self.assertRaises(booking.BookingError):
            booking.enrol(self.member, self.juniors)
        self.juniors.is_active = False
        self.juniors.save()
        with self.assertRaises(booking.BookingError):
            booking.enrol(self.other, self.juniors)
        self.assertEqual(len(mail.outbox), 1)

    def test_enrolling_replaces_single_session_bookings(self):
        booking.book_session(self.member, self.session)
        booking.enrol(self.member, self.juniors)
        self.assertFalse(SessionBooking.objects.exists())

    def test_leaving_removes_the_enrolment_and_emails(self):
        booking.enrol(self.member, self.juniors)
        mail.outbox.clear()
        booking.cancel_enrolment(self.member, self.juniors)
        self.assertFalse(ClassMember.objects.exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('left Juniors', mail.outbox[0].subject)

    def test_full_class_refuses_new_members(self):
        self.juniors.max_capacity = 1
        self.juniors.save()
        booking.enrol(self.member, self.juniors)
        with self.assertRaises(booking.BookingError):
            booking.enrol(self.other, self.juniors)


class SessionBookingTests(BookingTestBase):
    def test_book_and_cancel_email_the_member(self):
        booking.book_session(self.member, self.session)
        self.assertTrue(SessionBooking.objects.filter(member=self.member, session=self.session).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Booked: Juniors', mail.outbox[0].subject)
        booking.cancel_session_booking(self.member, self.session)
        self.assertFalse(SessionBooking.objects.exists())
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn('Booking cancelled', mail.outbox[1].subject)

    def test_booked_member_appears_on_that_register_only(self):
        other_session = Session.objects.create(assigned_class=self.juniors, date=self.session.date + timedelta(days=7))
        booking.book_session(self.member, self.session)
        names = [cm.member.name for cm in register_enrolments(self.juniors, self.session)]
        self.assertEqual(names, ['Alice Able'])
        self.assertEqual(register_enrolments(self.juniors, other_session), [])

    def test_auto_enrolled_members_are_on_every_register_and_need_no_booking(self):
        booking.enrol(self.member, self.juniors)
        self.assertEqual(len(register_enrolments(self.juniors, self.session)), 1)
        with self.assertRaises(booking.BookingError):
            booking.book_session(self.member, self.session)

    def test_enrolled_but_not_auto_enrolled_member_books_per_session(self):
        ClassMember.objects.create(assigned_class=self.juniors, member=self.member, auto_enrol=False)
        self.assertEqual(register_enrolments(self.juniors, self.session), [])
        booking.book_session(self.member, self.session)
        self.assertEqual([cm.member for cm in register_enrolments(self.juniors, self.session)], [self.member])

    def test_cannot_double_book_or_book_a_full_or_cancelled_session(self):
        booking.book_session(self.member, self.session)
        with self.assertRaises(booking.BookingError):
            booking.book_session(self.member, self.session)
        self.juniors.max_capacity = 1
        self.juniors.save()
        with self.assertRaises(booking.BookingError):
            booking.book_session(self.other, self.session)
        self.session.is_cancelled = True
        self.session.save()
        self.juniors.max_capacity = None
        self.juniors.save()
        with self.assertRaises(booking.BookingError):
            booking.book_session(self.other, self.session)

    def test_cut_off_blocks_booking_and_cancelling(self):
        soon = Session.objects.create(assigned_class=self.juniors, date=timezone.localdate())
        with self.assertRaises(booking.BookingError):
            booking.book_session(self.member, soon)
        SessionBooking.objects.create(session=soon, member=self.member)
        with self.assertRaises(booking.BookingError):
            booking.cancel_session_booking(self.member, soon)
        self.assertNotIn(soon, booking.bookable_sessions(self.juniors))
        self.assertIn(self.session, booking.bookable_sessions(self.juniors))

    def test_cut_off_comes_from_the_club_setting(self):
        self.assertEqual(self.org.booking_cutoff_hours, 2)
        self.org.settings = {'booking_cutoff_hours': 168}
        self.org.save()
        self.assertTrue(booking.changes_closed(Session.objects.get(pk=self.session.pk)))


class PortalBookingTests(BookingTestBase):
    def test_page_shows_active_classes_with_descriptions_only(self):
        Class.objects.create(organisation=self.org, name='Seniors', description='Hidden one', is_active=False)
        response = self.client.get(self.portal('portal_bookings'))
        self.assertContains(response, 'Juniors')
        self.assertContains(response, 'Fun judo for 7 to 11 year olds.')
        self.assertNotContains(response, 'Hidden one')

    def test_sessions_are_listed_by_day_with_calendar_data(self):
        response = self.client.get(self.portal('portal_bookings'))
        self.assertContains(response, f'data-date="{self.session.date.isoformat()}"')
        self.assertEqual(response.context['calendar_days'], {self.session.date.isoformat(): 1})
        self.assertContains(response, 'id="cal-grid"')

    def test_nav_link_only_when_a_class_is_active(self):
        self.assertContains(self.client.get(self.portal('portal_classes')), self.portal('portal_bookings'))
        Class.objects.update(is_active=False)
        self.assertNotContains(self.client.get(self.portal('portal_classes')), self.portal('portal_bookings'))

    def test_member_enrols_books_and_cancels_from_the_portal(self):
        self.act(action='book', session_id=self.session.pk)
        self.assertTrue(SessionBooking.objects.filter(member=self.member).exists())
        self.act(action='cancel_booking', session_id=self.session.pk)
        self.assertFalse(SessionBooking.objects.exists())
        self.act(action='enrol', class_id=self.juniors.pk)
        self.assertTrue(ClassMember.objects.filter(member=self.member).exists())
        self.act(action='leave', class_id=self.juniors.pk)
        self.assertFalse(ClassMember.objects.exists())
        self.assertEqual(len(mail.outbox), 4)

    def test_errors_are_shown_not_raised(self):
        self.juniors.is_active = False
        self.juniors.save()
        response = self.client.post(self.portal('portal_booking_action'), {'action': 'enrol', 'class_id': self.juniors.pk},
                                    follow=True)
        self.assertContains(response, 'isn&#x27;t open for booking')
        self.assertFalse(ClassMember.objects.exists())

    def test_other_clubs_classes_and_sessions_are_not_reachable(self):
        other_org = Organisation.objects.create(name='Other', slug='other', features=dict(ALL_ON))
        foreign = Class.objects.create(organisation=other_org, name='Foreign', is_active=True)
        foreign_session = Session.objects.create(assigned_class=foreign, date=self.session.date)
        self.assertEqual(self.act(action='enrol', class_id=foreign.pk).status_code, 404)
        self.assertEqual(self.act(action='book', session_id=foreign_session.pk).status_code, 404)


class StaffControlTests(BookingTestBase):
    def setUp(self):
        self.client.force_login(self.admin)

    def url(self, name, **kwargs):
        return reverse(name, kwargs={'org_slug': self.org.slug, **kwargs})

    def test_staff_toggle_auto_enrol_from_the_member_page(self):
        cm = ClassMember.objects.create(assigned_class=self.juniors, member=self.member)
        response = self.client.get(self.url('member_detail', pk=self.member.pk))
        self.assertContains(response, 'On every register')
        response = self.client.post(
            self.url('class_set_auto_enrol', pk=self.juniors.pk, member_pk=self.member.pk),
            {'auto_enrol': '0', 'next': 'member'},
        )
        self.assertRedirects(response, self.url('member_detail', pk=self.member.pk), fetch_redirect_response=False)
        cm.refresh_from_db()
        self.assertFalse(cm.auto_enrol)
        self.assertContains(self.client.get(self.url('member_detail', pk=self.member.pk)), 'Only on sessions they book')

    def test_register_lists_booked_members_with_a_badge(self):
        booking.book_session(self.member, self.session)
        response = self.client.get(self.url('session_register', pk=self.juniors.pk, session_pk=self.session.pk))
        self.assertContains(response, 'Alice Able')
        self.assertContains(response, 'Booked')

    def test_saving_the_register_records_attendance_for_booked_members(self):
        booking.book_session(self.member, self.session)
        self.client.post(
            self.url('session_register', pk=self.juniors.pk, session_pk=self.session.pk),
            {'present': [self.member.pk]},
        )
        self.assertTrue(self.session.attendance.get(member=self.member).present)

    def test_class_form_saves_the_active_toggle(self):
        response = self.client.post(self.url('class_edit', pk=self.juniors.pk), {
            'name': 'Juniors', 'description': 'New words',
        })
        self.assertEqual(response.status_code, 302)
        self.juniors.refresh_from_db()
        self.assertFalse(self.juniors.is_active)
        self.assertEqual(self.juniors.description, 'New words')

    def test_booking_cutoff_setting_is_saved_and_validated(self):
        self.client.post(self.url('org_settings'), {'action': 'features', 'features': ['member_portal'],
                                                    'booking_cutoff_hours': '6'})
        self.org.refresh_from_db()
        self.assertEqual(self.org.booking_cutoff_hours, 6)
        self.client.post(self.url('org_settings'), {'action': 'features', 'features': ['member_portal'],
                                                    'booking_cutoff_hours': '9999'})
        self.org.refresh_from_db()
        self.assertEqual(self.org.booking_cutoff_hours, 6)


class RestrictionTests(BookingTestBase):
    def born(self, years, extra_days=0):
        return timezone.localdate().replace(year=timezone.localdate().year - years) - timedelta(days=extra_days)

    def test_age_range_limits_enrolling_and_booking(self):
        self.juniors.min_age, self.juniors.max_age = 7, 11
        self.juniors.save()
        self.member.date_of_birth = self.born(9)
        self.member.save()
        booking.book_session(self.member, self.session)
        self.other.date_of_birth = self.born(14)
        self.other.save()
        for attempt in (lambda: booking.enrol(self.other, self.juniors),
                        lambda: booking.book_session(self.other, self.session)):
            with self.assertRaises(booking.BookingError):
                attempt()

    def test_age_limit_is_inclusive_and_needs_a_birth_date(self):
        self.juniors.max_age = 11
        self.juniors.save()
        self.member.date_of_birth = self.born(11, extra_days=30)
        self.member.save()
        self.assertEqual(self.juniors.ineligible_reason(self.member), '')
        self.member.date_of_birth = self.born(12)
        self.assertNotEqual(self.juniors.ineligible_reason(self.member), '')
        self.member.date_of_birth = None
        self.assertIn('date of birth', self.juniors.ineligible_reason(self.member))

    def test_grade_range_uses_the_highest_grade_in_the_system(self):
        system = ProgressionSystem.objects.create(organisation=self.org, name='Belts')
        white, orange, green = [ProgressionStage.objects.create(system=system, name=n, order=i)
                                for i, n in enumerate(['White', 'Orange', 'Green'])]
        self.juniors.min_stage, self.juniors.max_stage = orange, orange
        self.juniors.save()
        self.assertNotEqual(self.juniors.ineligible_reason(self.member), '')  # no grade yet
        MemberProgression.objects.create(member=self.member, stage=white, achieved_date=timezone.localdate())
        self.assertNotEqual(self.juniors.ineligible_reason(self.member), '')
        MemberProgression.objects.create(member=self.member, stage=orange, achieved_date=timezone.localdate())
        self.assertEqual(self.juniors.ineligible_reason(self.member), '')
        MemberProgression.objects.create(member=self.member, stage=green, achieved_date=timezone.localdate())
        self.assertNotEqual(self.juniors.ineligible_reason(self.member), '')

    def test_classes_outside_the_range_are_not_shown_in_the_portal(self):
        self.juniors.max_age = 11
        self.juniors.save()
        self.member.date_of_birth = self.born(9)
        self.member.save()
        self.assertContains(self.client.get(self.portal('portal_bookings')), 'Ages 11 and under')
        self.member.date_of_birth = self.born(15)
        self.member.save()
        response = self.client.get(self.portal('portal_bookings'))
        self.assertNotContains(response, 'Juniors')
        self.assertNotContains(response, 'Enrol in this class')
        self.act(action='enrol', class_id=self.juniors.pk)
        self.assertFalse(ClassMember.objects.exists())

    def test_staff_can_save_limits_and_bad_ranges_are_refused(self):
        self.client.force_login(self.admin)
        url = reverse('class_edit', kwargs={'org_slug': self.org.slug, 'pk': self.juniors.pk})
        self.client.post(url, {'name': 'Juniors', 'min_age': '7', 'max_age': '11'})
        self.juniors.refresh_from_db()
        self.assertEqual((self.juniors.min_age, self.juniors.max_age), (7, 11))
        response = self.client.post(url, {'name': 'Juniors', 'min_age': '12', 'max_age': '8'})
        self.assertEqual(response.status_code, 200)
        self.juniors.refresh_from_db()
        self.assertEqual(self.juniors.min_age, 7)


class SkippingSessionTests(BookingTestBase):
    def setUp(self):
        booking.enrol(self.member, self.juniors)
        mail.outbox.clear()

    def test_enrolled_member_can_skip_one_session_and_is_off_that_register(self):
        booking.skip_session(self.member, self.session)
        self.assertTrue(SessionAbsence.objects.filter(member=self.member, session=self.session).exists())
        self.assertEqual(register_enrolments(self.juniors, self.session), [])
        other_session = Session.objects.create(assigned_class=self.juniors, date=self.session.date + timedelta(days=7))
        self.assertEqual(len(register_enrolments(self.juniors, other_session)), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Booking cancelled', mail.outbox[0].subject)

    def test_skipping_frees_the_place_and_undo_takes_it_back(self):
        self.juniors.max_capacity = 1
        self.juniors.save()
        self.assertTrue(booking.session_is_full(self.session))
        booking.skip_session(self.member, self.session)
        self.assertFalse(booking.session_is_full(self.session))
        booking.book_session(self.other, self.session)
        with self.assertRaises(booking.BookingError):
            booking.rejoin_session(self.member, self.session)
        booking.cancel_session_booking(self.other, self.session)
        booking.rejoin_session(self.member, self.session)
        self.assertFalse(SessionAbsence.objects.exists())

    def test_only_enrolled_members_can_skip_and_not_after_the_cut_off(self):
        with self.assertRaises(booking.BookingError):
            booking.skip_session(self.other, self.session)
        soon = Session.objects.create(assigned_class=self.juniors, date=timezone.localdate())
        with self.assertRaises(booking.BookingError):
            booking.skip_session(self.member, soon)

    def test_portal_lists_enrolled_sessions_with_cant_attend_and_undo(self):
        response = self.client.get(self.portal('portal_bookings'))
        self.assertContains(response, 'Can&#x27;t attend')
        self.act(action='skip_session', session_id=self.session.pk)
        self.assertTrue(SessionAbsence.objects.exists())
        self.assertContains(self.client.get(self.portal('portal_bookings')), 'not attending')
        self.act(action='rejoin_session', session_id=self.session.pk)
        self.assertFalse(SessionAbsence.objects.exists())

    def test_leaving_the_class_clears_the_absences(self):
        booking.skip_session(self.member, self.session)
        booking.cancel_enrolment(self.member, self.juniors)
        self.assertFalse(SessionAbsence.objects.exists())


class BulkGenerateTests(BookingTestBase):
    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse('class_bulk_generate', kwargs={'org_slug': self.org.slug})
        day = timezone.localdate().weekday()
        self.juniors.schedule = [{'day': day, 'time': '18:00'}]
        self.juniors.save()
        self.seniors = Class.objects.create(organisation=self.org, name='Seniors', schedule=[{'day': day, 'time': '19:00'}])
        self.unscheduled = Class.objects.create(organisation=self.org, name='Open mat')

    def test_page_lists_classes_and_disables_unscheduled_ones(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Juniors')
        self.assertContains(response, 'Open mat')
        self.assertContains(response, "No weekly schedule")

    def test_generates_only_for_the_chosen_classes(self):
        self.client.post(self.url, {'classes': [self.seniors.pk, self.unscheduled.pk], 'weeks': '3'})
        self.assertEqual(self.seniors.sessions.count(), 3)
        self.assertEqual(self.juniors.sessions.count(), 1)  # only the one made in setUpTestData
        self.assertFalse(self.unscheduled.sessions.exists())

    def test_generating_twice_does_not_duplicate_and_nothing_chosen_warns(self):
        self.client.post(self.url, {'classes': [self.seniors.pk], 'weeks': '2'})
        self.client.post(self.url, {'classes': [self.seniors.pk], 'weeks': '2'})
        self.assertEqual(self.seniors.sessions.count(), 2)
        response = self.client.post(self.url, {'weeks': '2'}, follow=True)
        self.assertContains(response, 'Choose at least one class')

    def test_other_clubs_classes_are_ignored(self):
        other_org = Organisation.objects.create(name='Other', slug='other', features=dict(ALL_ON))
        foreign = Class.objects.create(organisation=other_org, name='Foreign', schedule=[{'day': 0, 'time': '18:00'}])
        self.client.post(self.url, {'classes': [foreign.pk, self.seniors.pk], 'weeks': '2'})
        self.assertFalse(foreign.sessions.exists())
