"""Feature #168 S3 — patterns deploy as skills into the codebuddy family dirs.

Runs under a tmp HOME so `~/.codebuddy/skills` and `~/.workbuddy/skills`
resolve into tmp_path; the active provider stays claude throughout.
"""

from __future__ import annotations

import yaml
import pytest

from lib import settings as settings_mod
from lib.patterns import pattern_deployments
from lib.providers import build_provider
from lib.skills import skill_sync

SLUG = 'login-fix'
FAMILY = {'codebuddy': '.codebuddy', 'workbuddy': '.workbuddy'}


@pytest.fixture
def env(tmp_db, tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    patterns = tmp_path / 'patterns'
    src = patterns / SLUG
    src.mkdir(parents=True)
    (src / 'SKILL.md').write_text(
        '---\ntitle: Login fix\nprocedure: login-fix\n'
        'description: Repair the login flow without touching sessions\n---\n'
        '## Steps\n\nRead the auth blueprint first.\n',
        encoding='utf-8',
    )
    (src / 'references').mkdir()
    (src / 'references' / 'notes.md').write_text('ref', encoding='utf-8')
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setattr(settings_mod.settings, 'patterns_dir', str(patterns))
    monkeypatch.setattr(settings_mod.settings, 'codebuddy_config_dir', None)
    monkeypatch.setattr(settings_mod.settings, 'workbuddy_config_dir', None)
    monkeypatch.setattr(settings_mod.settings, 'active_provider', 'claude')
    monkeypatch.setattr(settings_mod.settings, 'providers', {})
    return home


def _frontmatter(path) -> dict:
    _, fm, _ = path.read_text(encoding='utf-8').split('---', 2)
    return yaml.safe_load(fm)


def _assert_deployed_skill(home, root: str) -> None:
    skill_md = home / root / 'skills' / SLUG / 'SKILL.md'
    assert skill_md.is_file(), root
    assert _frontmatter(skill_md) == {
        'name': SLUG,
        'description': 'Repair the login flow without touching sessions',
    }
    assert (skill_md.parent / 'references' / 'notes.md').is_file()


def _ledger_rows() -> dict:
    rows = pattern_deployments.list_deployments(pattern_slug=SLUG)
    return {r['provider']: (r['scope'], r['deployed_path']) for r in rows}


def test_skills_deploy_to_codebuddy_family_dirs(env):
    messages = [skill_sync.push(SLUG, provider_id=pid) for pid in FAMILY]
    assert all(m.startswith('pushed pattern') for m in messages), messages

    for root in FAMILY.values():
        _assert_deployed_skill(env, root)
    active_dir = build_provider('claude').global_skills_dir() / SLUG
    assert not active_dir.exists()
    assert _ledger_rows() == {
        pid: ('global', str(env / root / 'skills' / SLUG)) for pid, root in FAMILY.items()
    }


def test_redeploy_to_a_family_dir_is_idempotent(env):
    skill_sync.push(SLUG, provider_id='workbuddy')
    msg = skill_sync.push(SLUG, provider_id='workbuddy')
    assert msg.startswith('pushed pattern'), msg
    assert len(pattern_deployments.list_deployments(pattern_slug=SLUG)) == 1


def test_skillless_provider_is_refused(env, monkeypatch):
    from lib.providers.codebuddy import CodeBuddyProvider
    from lib.providers.base import ProviderCapabilities
    monkeypatch.setattr(CodeBuddyProvider, 'capabilities',
                        ProviderCapabilities(skills=False, hooks=True))
    msg = skill_sync.push(SLUG, provider_id='codebuddy')
    assert msg.startswith('refused:'), msg
    assert not (env / '.codebuddy').exists()
