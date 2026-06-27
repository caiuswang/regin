"""Tests for the env-var session-id resolver in lib/session_probe.py."""

from lib import session_probe


def test_resolve_returns_env_var(monkeypatch):
    monkeypatch.setenv(session_probe._ENV_SESSION_ID, 'env-sid')
    assert session_probe.resolve() == 'env-sid'


def test_resolve_returns_none_when_env_absent(monkeypatch):
    monkeypatch.delenv(session_probe._ENV_SESSION_ID, raising=False)
    assert session_probe.resolve() is None


def test_resolve_treats_empty_env_as_miss(monkeypatch):
    # An exported-but-empty var is a miss, not the empty string — callers rely
    # on falsy stdout meaning "omit the flag".
    monkeypatch.setenv(session_probe._ENV_SESSION_ID, '')
    assert session_probe.resolve() is None
