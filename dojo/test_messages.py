from django.contrib import messages
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from organisations.models import Organisation, OrganisationMember


class ErrorMessageStylingTests(TestCase):
    def test_error_messages_use_bootstraps_danger_style(self):
        org = Organisation.objects.create(name='Test Club', slug='test-club')
        user = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=user, organisation=org, role='org_admin')
        self.client.force_login(user)

        # The general settings form reports a missing name as an error message.
        response = self.client.post(
            reverse('org_settings', kwargs={'org_slug': org.slug}), {'action': 'general', 'name': ''},
        )
        self.assertEqual([m.level for m in response.context['messages']], [messages.ERROR])
        self.assertContains(response, 'alert alert-danger')
        self.assertNotContains(response, 'alert-error')
