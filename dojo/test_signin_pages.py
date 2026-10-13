from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import TwoFactorDevice
from organisations.models import Organisation, OrganisationMember


class SignInPagesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Forest Judo', slug='forest', settings={
            'sidebar_color': '#1A3C34', 'sidebar_color_dark': '#14302a',
            'accent_color': '#059669', 'accent_hover': '#047857',
        })

    def test_single_club_pages_use_its_colours_and_say_powered_by_dojo(self):
        for name in ('login', 'password_reset', 'password_reset_done', 'password_reset_complete'):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertContains(response, '--dojo-sidebar: #1A3C34;')
                self.assertContains(response, 'Forest Judo')
                self.assertContains(response, 'Powered by')
                self.assertContains(response, 'img/dojo-wordmark.png')

    def test_two_step_pages_match(self):
        user = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=user, organisation=self.org, role='coach')
        TwoFactorDevice.objects.create(user=user, secret='JBSWY3DPEHPK3PXP')
        self.client.force_login(user)
        response = self.client.get(reverse('two_factor_verify'))
        self.assertContains(response, '--dojo-sidebar: #1A3C34;')
        self.assertContains(response, 'Powered by')
        self.assertContains(response, 'Sign out')

    def test_several_clubs_show_dojo_itself(self):
        Organisation.objects.create(name='Other Club', slug='other')
        response = self.client.get(reverse('login'))
        self.assertNotContains(response, '--dojo-sidebar: #1A3C34;')
        self.assertNotContains(response, 'Forest Judo')
        self.assertNotContains(response, 'Powered by')
        self.assertContains(response, 'img/dojo-wordmark.png')
        self.assertContains(response, 'Club &amp; class management')
