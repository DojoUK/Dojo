"""
Which Dojo this is, for the settings page: an official release, an official
build between releases ("beta"), or an unofficial fork.

Read straight from the .git folder (the Docker image has no git binary), or
from DOJO_BUILD_REPO / DOJO_BUILD_COMMIT / DOJO_BUILD_TAG for deployments
built without one. Informational only: anyone can edit their own copy.
"""
import os
import re
import zlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from django.conf import settings

OFFICIAL_REPO = 'DojoUK/Dojo'
RELEASES_URL = 'https://github.com/DojoUK/Dojo/releases'


@dataclass(frozen=True)
class VersionInfo:
    kind: str           # 'release', 'beta', 'fork' or 'unknown'
    repo: str           # e.g. 'DojoUK/Dojo' or 'WeaversJ/Dojo'; '' if unknown
    commit: str         # full commit hash; '' if unknown
    tag: str            # release tag at this commit; '' between releases
    is_official: bool

    @property
    def short_commit(self):
        return self.commit[:7]

    @property
    def label(self):
        if self.kind == 'release':
            return f'Dojo {self.tag}'
        if self.kind == 'beta':
            return 'Dojo beta'
        if self.kind == 'fork':
            return 'Unofficial Dojo fork'
        return 'Dojo (version unknown)'

    @property
    def status(self):
        """Short status for a build that isn't a release: 'Beta' or 'Unofficial fork'."""
        return {'beta': 'Beta', 'fork': 'Unofficial fork'}.get(self.kind, '')

    @property
    def short(self):
        """One line for the sidebar: "Dojo v1.0.0", "Beta · b9da68d" or "Unofficial fork · 4f9c72d"."""
        if self.kind == 'release':
            return self.label
        if self.status:
            return f'{self.status} · {self.short_commit}' if self.short_commit else self.status
        return ''


def repo_from_url(url):
    """'owner/name' from a GitHub-style remote URL, e.g. git@github.com:DojoUK/Dojo.git."""
    match = re.search(r'[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$', (url or '').strip())
    return f'{match.group(1)}/{match.group(2)}' if match else ''


def _git_dir(root):
    git = Path(root) / '.git'
    if git.is_file():  # a worktree: ".git" is a file pointing at the real directory
        target = git.read_text().strip().removeprefix('gitdir:').strip()
        git = (Path(root) / target).resolve()
    return git if git.is_dir() else None


def _common_dir(git):
    """For worktrees, refs and config live in the main repository's .git."""
    common = git / 'commondir'
    return (git / common.read_text().strip()).resolve() if common.is_file() else git


def _packed_refs(common):
    refs, peeled, last = {}, {}, None
    path = common / 'packed-refs'
    if path.is_file():
        for line in path.read_text().splitlines():
            if line.startswith('#') or not line.strip():
                continue
            if line.startswith('^'):  # the commit an annotated tag on the previous line points to
                if last:
                    peeled[last] = line[1:].strip()
                continue
            sha, name = line.split(' ', 1)
            refs[name.strip()] = sha
            last = name.strip()
    return refs, peeled


def _resolve(git, common, ref, packed):
    for base in (git, common):
        path = base / ref
        if path.is_file():
            return path.read_text().strip()
    return packed.get(ref, '')


def _peel_loose_tag(common, sha):
    """The commit an annotated tag object points to, if the object is stored loose."""
    path = common / 'objects' / sha[:2] / sha[2:]
    if not path.is_file():
        return sha
    data = zlib.decompress(path.read_bytes())
    if data.startswith(b'tag '):
        match = re.search(rb'\nobject ([0-9a-f]{40})|^tag \d+\x00object ([0-9a-f]{40})', data)
        if match:
            return (match.group(1) or match.group(2)).decode()
    return sha


def _origin_url(common):
    config = common / 'config'
    if not config.is_file():
        return ''
    section = None
    for line in config.read_text().splitlines():
        line = line.strip()
        if line.startswith('['):
            section = line
        elif section == '[remote "origin"]' and line.startswith('url'):
            return line.split('=', 1)[1].strip()
    return ''


def _version_key(tag):
    return tuple(int(n) for n in re.findall(r'\d+', tag)) or (0,)


def read_git(root):
    """(repo, commit, release tag at HEAD or '') from a checkout's .git folder."""
    git = _git_dir(root)
    if git is None:
        return '', '', ''
    common = _common_dir(git)
    packed, peeled = _packed_refs(common)

    head = (git / 'HEAD').read_text().strip()
    commit = _resolve(git, common, head[5:].strip(), packed) if head.startswith('ref:') else head

    tags = {}
    for name, sha in packed.items():
        if name.startswith('refs/tags/'):
            tags[name[len('refs/tags/'):]] = peeled.get(name, sha)
    loose = common / 'refs' / 'tags'
    if loose.is_dir():
        for path in loose.rglob('*'):
            if path.is_file():
                tags[str(path.relative_to(loose))] = _peel_loose_tag(common, path.read_text().strip())

    exact = sorted((t for t, sha in tags.items() if sha == commit), key=_version_key)
    return repo_from_url(_origin_url(common)), commit, (exact[-1] if exact else '')


def build_info(repo, commit, exact_tag):
    official = repo.lower() == OFFICIAL_REPO.lower()
    if not repo and not commit:
        kind = 'unknown'
    elif not official:
        kind = 'fork'
    elif exact_tag:
        kind = 'release'
    else:
        kind = 'beta'
    return VersionInfo(kind=kind, repo=repo, commit=commit, tag=exact_tag, is_official=official)


@lru_cache(maxsize=1)
def get_version_info():
    repo = os.environ.get('DOJO_BUILD_REPO', '').strip()
    commit = os.environ.get('DOJO_BUILD_COMMIT', '').strip()
    tag = os.environ.get('DOJO_BUILD_TAG', '').strip()
    if repo or commit:
        return build_info(repo_from_url(repo) or repo, commit, tag)
    try:
        return build_info(*read_git(settings.BASE_DIR))
    except (OSError, ValueError, zlib.error):
        return build_info('', '', '')
