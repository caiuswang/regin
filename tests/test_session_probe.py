"""Tests for the session-id memory-cache in lib/session_probe.py."""

import time

import pytest

from lib import session_probe


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    from lib.settings import settings
    monkeypatch.setattr(settings, 'data_dir', tmp_path, raising=False)
    # The suite itself runs inside Claude Code, which exports this; clear it so
    # the cache-behavior tests are deterministic. Env-preference tests set it
    # back explicitly.
    monkeypatch.delenv(session_probe._ENV_SESSION_ID, raising=False)
    yield


def test_record_then_resolve_by_cwd():
    session_probe.record('sid-1', cwd='/a')
    assert session_probe.resolve(cwd='/a') == 'sid-1'


def test_resolve_misses_return_none():
    assert session_probe.resolve(cwd='/nope') is None


def test_latest_stamp_wins_for_a_cwd():
    session_probe.record('old', cwd='/a', ts=time.time() - 10)
    session_probe.record('new', cwd='/a', ts=time.time())
    assert session_probe.resolve(cwd='/a') == 'new'


def test_nonce_extracted_from_command_and_resolved():
    session_probe.record('sid-n', cwd='/a', command='regin session-id --nonce ABC')
    assert session_probe.resolve(nonce='ABC') == 'sid-n'
    # nonce takes precedence over cwd
    session_probe.record('sid-cwd', cwd='/a')
    assert session_probe.resolve(cwd='/a', nonce='ABC') == 'sid-n'


def test_stale_entries_are_not_returned():
    session_probe.record('ancient', cwd='/a', ts=time.time() - session_probe._MAX_AGE_S - 1)
    assert session_probe.resolve(cwd='/a') is None


def test_cwd_miss_falls_back_only_when_single_session():
    # One distinct session live → a cwd miss safely returns it.
    session_probe.record('only-sid', cwd='/x', ts=time.time() - 5)
    session_probe.record('only-sid', cwd='/y', ts=time.time())
    assert session_probe.resolve(cwd='/unknown') == 'only-sid'


def test_cwd_miss_returns_none_when_multiple_sessions_live():
    # Two distinct sessions → never guess; a cwd miss returns None.
    session_probe.record('sid-x', cwd='/x', ts=time.time() - 5)
    session_probe.record('sid-y', cwd='/y', ts=time.time())
    assert session_probe.resolve(cwd='/unknown') is None
    # Exact cwd matches still resolve correctly.
    assert session_probe.resolve(cwd='/x') == 'sid-x'
    assert session_probe.resolve(cwd='/y') == 'sid-y'


def test_env_var_is_preferred_over_cwd_cache(monkeypatch):
    # The cwd cache says 'sibling', but the authoritative env var wins — this
    # is the fix for `regin session-id` returning a clobbered sibling id.
    session_probe.record('sibling', cwd='/a')
    monkeypatch.setenv(session_probe._ENV_SESSION_ID, 'env-sid')
    assert session_probe.resolve(cwd='/a') == 'env-sid'


def test_env_var_resolves_on_a_cache_miss(monkeypatch):
    monkeypatch.setenv(session_probe._ENV_SESSION_ID, 'env-sid')
    assert session_probe.resolve(cwd='/unknown') == 'env-sid'


def test_explicit_nonce_wins_over_env(monkeypatch):
    session_probe.record('sid-n', cwd='/a', command='x --nonce ABC')
    monkeypatch.setenv(session_probe._ENV_SESSION_ID, 'env-sid')
    assert session_probe.resolve(cwd='/a', nonce='ABC') == 'sid-n'


def test_falls_back_to_cache_when_env_absent(monkeypatch):
    monkeypatch.delenv(session_probe._ENV_SESSION_ID, raising=False)
    session_probe.record('cached', cwd='/a')
    assert session_probe.resolve(cwd='/a') == 'cached'


def test_record_without_session_is_noop():
    session_probe.record(None, cwd='/a')
    assert session_probe.resolve(cwd='/a') is None


def test_corrupt_cache_file_is_tolerated():
    (session_probe._cache_path()).write_text('{ not json', encoding='utf-8')
    # resolve must not raise on a corrupt file; record must heal it.
    assert session_probe.resolve(cwd='/a') is None
    session_probe.record('sid-ok', cwd='/a')
    assert session_probe.resolve(cwd='/a') == 'sid-ok'
