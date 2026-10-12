import os
import tempfile
import zlib
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from dojo import version
from organisations.models import Organisation, OrganisationMember

COMMIT_A = 'a' * 40
COMMIT_B = 'b' * 40
TAG_OBJECT = 'c' * 40


def make_repo(origin, head_commit, packed='', loose_tags=None, loose_objects=None):
    """A minimal .git folder: HEAD on main, an origin remote, and optional tags."""
    root = Path(tempfile.mkdtemp())
    git = root / '.git'
    (git / 'refs' / 'heads').mkdir(parents=True)
    (git / 'HEAD').write_text('ref: refs/heads/main\n')
    (git / 'refs' / 'heads' / 'main').write_text(head_commit + '\n')
    (git / 'config').write_text(f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {origin}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n')
    if packed:
        (git / 'packed-refs').write_text('# pack-refs with: peeled fully-peeled sorted\n' + packed)
    for name, sha in (loose_tags or {}).items():
        (git / 'refs' / 'tags').mkdir(parents=True, exist_ok=True)
        (git / 'refs' / 'tags' / name).write_text(sha + '\n')
    for sha, body in (loose_objects or {}).items():
        (git / 'objects' / sha[:2]).mkdir(parents=True, exist_ok=True)
        (git / 'objects' / sha[:2] / sha[2:]).write_bytes(zlib.compress(body))
    return root


def info_for(root):
    return version.build_info(*version.read_git(root))


class RepoFromUrlTests(SimpleTestCase):
    def test_common_remote_forms(self):
        for url in ('git@github.com:DojoUK/Dojo.git', 'https://github.com/DojoUK/Dojo.git',
                    'https://github.com/DojoUK/Dojo', 'ssh://git@github.com/DojoUK/Dojo.git'):
            with self.subTest(url=url):
                self.assertEqual(version.repo_from_url(url), 'DojoUK/Dojo')


class ReadGitTests(SimpleTestCase):
    def test_official_release(self):
        root = make_repo('git@github.com:DojoUK/Dojo.git', COMMIT_A, packed=f'{COMMIT_A} refs/tags/v1.0.0\n')
        info = info_for(root)
        self.assertEqual((info.kind, info.label, info.tag), ('release', 'Dojo v1.0.0', 'v1.0.0'))

    def test_official_between_releases_is_beta(self):
        root = make_repo('https://github.com/dojouk/dojo.git', COMMIT_B,
                         packed=f'{COMMIT_A} refs/tags/v0.9.0\n{COMMIT_A} refs/tags/v0.10.0\n')
        info = info_for(root)
        self.assertEqual((info.kind, info.tag, info.short), ('beta', '', 'Beta · bbbbbbb'))

    def test_any_other_repository_is_an_unofficial_fork(self):
        root = make_repo('git@github.com:WeaversJ/Dojo.git', COMMIT_A, packed=f'{COMMIT_A} refs/tags/v1.0.0\n')
        info = info_for(root)
        self.assertEqual((info.kind, info.label, info.repo), ('fork', 'Unofficial Dojo fork', 'WeaversJ/Dojo'))
        self.assertFalse(info.is_official)

    def test_packed_annotated_tag_is_peeled_to_its_commit(self):
        root = make_repo('git@github.com:DojoUK/Dojo.git', COMMIT_A,
                         packed=f'{TAG_OBJECT} refs/tags/v2.0.0\n^{COMMIT_A}\n')
        self.assertEqual(info_for(root).label, 'Dojo v2.0.0')

    def test_loose_annotated_tag_is_peeled_to_its_commit(self):
        body = f'object {COMMIT_A}\ntype commit\ntag v3.0.0\n'.encode()
        root = make_repo('git@github.com:DojoUK/Dojo.git', COMMIT_A,
                         loose_tags={'v3.0.0': TAG_OBJECT},
                         loose_objects={TAG_OBJECT: b'tag %d\x00' % len(body) + body})
        self.assertEqual(info_for(root).label, 'Dojo v3.0.0')

    def test_worktree_checkout(self):
        main = make_repo('git@github.com:DojoUK/Dojo.git', COMMIT_A, packed=f'{COMMIT_A} refs/tags/v1.0.0\n')
        worktree_git = main / '.git' / 'worktrees' / 'wt'
        worktree_git.mkdir(parents=True)
        (worktree_git / 'HEAD').write_text(COMMIT_A + '\n')
        (worktree_git / 'commondir').write_text('../..\n')
        wt = Path(tempfile.mkdtemp())
        (wt / '.git').write_text(f'gitdir: {worktree_git}\n')
        self.assertEqual(info_for(wt).label, 'Dojo v1.0.0')

    def test_no_git_folder_is_unknown(self):
        info = info_for(tempfile.mkdtemp())
        self.assertEqual((info.kind, info.label), ('unknown', 'Dojo (version unknown)'))


class EnvironmentOverrideTests(SimpleTestCase):
    def setUp(self):
        version.get_version_info.cache_clear()
        self.addCleanup(version.get_version_info.cache_clear)

    def test_build_variables_take_priority(self):
        env = {'DOJO_BUILD_REPO': 'https://github.com/DojoUK/Dojo', 'DOJO_BUILD_COMMIT': COMMIT_A, 'DOJO_BUILD_TAG': 'v1.2.0'}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(version.get_version_info().label, 'Dojo v1.2.0')

    def test_build_variables_for_a_fork(self):
        with mock.patch.dict(os.environ, {'DOJO_BUILD_REPO': 'WeaversJ/Dojo', 'DOJO_BUILD_COMMIT': COMMIT_A}):
            self.assertEqual(version.get_version_info().kind, 'fork')


class SettingsShowsVersionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name='Test Club', slug='test-club')
        cls.admin = User.objects.create_user('admin', password='pw')
        OrganisationMember.objects.create(user=cls.admin, organisation=cls.org, role='org_admin')

    def settings_page(self, info):
        self.client.force_login(self.admin)
        with mock.patch('organisations.views.get_version_info', return_value=info):
            return self.client.get(reverse('org_settings', kwargs={'org_slug': self.org.slug}))

    def test_fork_shows_a_build_box_marked_unofficial(self):
        response = self.settings_page(version.build_info('WeaversJ/Dojo', COMMIT_A, ''))
        self.assertContains(response, '<span class="build-status build-status-fork">Unofficial fork</span>', html=True)
        self.assertContains(response, 'WeaversJ/Dojo</span>')
        self.assertContains(response, 'aaaaaaa</span>')
        self.assertNotContains(response, '>Version</dt>')

    def test_beta_shows_a_build_box_marked_beta(self):
        response = self.settings_page(version.build_info('DojoUK/Dojo', COMMIT_B, ''))
        self.assertContains(response, '<span class="build-status build-status-beta">Beta</span>', html=True)
        self.assertContains(response, 'bbbbbbb</span>')
        self.assertNotContains(response, '>Version</dt>')

    def test_release_shows_only_the_version(self):
        response = self.settings_page(version.build_info('DojoUK/Dojo', COMMIT_A, 'v1.0.0'))
        self.assertContains(response, '<dd class="col-sm-8 mb-0">Dojo v1.0.0</dd>', html=True)
        self.assertNotContains(response, '>Build</dt>')


class SidebarShowsVersionTests(TestCase):
    def test_sidebar_footer_shows_the_version_above_the_licence(self):
        org = Organisation.objects.create(name='Test Club', slug='test-club')
        coach = User.objects.create_user('coach', password='pw')
        OrganisationMember.objects.create(user=coach, organisation=org, role='coach')
        self.client.force_login(coach)
        beta = version.build_info('DojoUK/Dojo', COMMIT_B, '')
        with mock.patch('dojo.version.get_version_info', return_value=beta):
            html = self.client.get(reverse('org_dashboard', kwargs={'org_slug': org.slug})).content.decode()
        self.assertIn('<div class="dojo-version"', html)
        self.assertIn('Beta · bbbbbbb</div>', html)
        self.assertLess(html.index('class="dojo-version"'), html.index('AGPL-3.0'))
