from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from classes.models import Class
from organisations.features import FEATURES_BY_KEY
from organisations.models import Organisation, OrganisationMember

ALL_ON = {key: True for key in FEATURES_BY_KEY}


class BulkGenerateTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name='Test Club', slug='test-club', features=dict(ALL_ON))
        admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=admin, organisation=self.org, role='org_admin')
        self.client.force_login(admin)
        self.url = reverse('class_bulk_generate', kwargs={'org_slug': self.org.slug})
        day = timezone.localdate().weekday()
        self.juniors = Class.objects.create(organisation=self.org, name='Juniors', schedule=[{'day': day, 'time': '18:00'}])
        self.seniors = Class.objects.create(organisation=self.org, name='Seniors', schedule=[{'day': day, 'time': '19:00'}])
        self.unscheduled = Class.objects.create(organisation=self.org, name='Open mat')

    def test_page_lists_classes_and_flags_unscheduled_ones(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Juniors')
        self.assertContains(response, 'Open mat')
        self.assertContains(response, 'No weekly schedule')

    def test_class_list_links_to_the_page(self):
        response = self.client.get(reverse('class_list', kwargs={'org_slug': self.org.slug}))
        self.assertContains(response, self.url)

    def test_generates_only_for_the_chosen_classes(self):
        self.client.post(self.url, {'classes': [self.seniors.pk, self.unscheduled.pk], 'weeks': '3'})
        self.assertEqual(self.seniors.sessions.count(), 3)
        self.assertFalse(self.juniors.sessions.exists())
        self.assertFalse(self.unscheduled.sessions.exists())

    def test_generating_twice_does_not_duplicate_and_nothing_chosen_warns(self):
        self.client.post(self.url, {'classes': [self.seniors.pk], 'weeks': '2'})
        self.client.post(self.url, {'classes': [self.seniors.pk], 'weeks': '2'})
        self.assertEqual(self.seniors.sessions.count(), 2)
        response = self.client.post(self.url, {'weeks': '2'}, follow=True)
        self.assertContains(response, 'Choose at least one class')

    def test_other_clubs_classes_are_ignored(self):
        other = Organisation.objects.create(name='Other', slug='other', features=dict(ALL_ON))
        foreign = Class.objects.create(organisation=other, name='Foreign', schedule=[{'day': 0, 'time': '18:00'}])
        self.client.post(self.url, {'classes': [foreign.pk, self.seniors.pk], 'weeks': '2'})
        self.assertFalse(foreign.sessions.exists())
