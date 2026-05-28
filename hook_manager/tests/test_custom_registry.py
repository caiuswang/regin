"""Tests for the custom_registry extension layer."""

import sys

from hook_manager.core import Handler
from hook_manager.registry import _load_custom_handlers


# ── With the shipped custom_registry present ──────────────────────────

def test_custom_handlers_is_a_list_of_handlers():
    from hook_manager.custom_registry import CUSTOM_HANDLERS
    assert isinstance(CUSTOM_HANDLERS, list)
    for h in CUSTOM_HANDLERS:
        assert isinstance(h, Handler)


# ── Missing custom_registry is not fatal ─────────────────────────────

def test_load_returns_empty_list_when_module_missing():
    """If a user deletes custom_registry.py, loading returns []."""
    out = _load_custom_handlers('hook_manager.this_module_does_not_exist')
    assert out == []


def test_load_returns_empty_list_when_module_has_no_custom_handlers(tmp_path, monkeypatch):
    """A user could create a custom_registry.py that just has other top-level
    code but no CUSTOM_HANDLERS name. Treat as empty, don't crash."""
    mod_name = 'hook_manager_test_empty_module_xyz'
    fake_module = type(sys)('fake')
    # Deliberately no CUSTOM_HANDLERS attribute.
    sys.modules[mod_name] = fake_module
    try:
        assert _load_custom_handlers(mod_name) == []
    finally:
        del sys.modules[mod_name]


def test_load_returns_empty_list_when_custom_handlers_is_not_a_list():
    """If someone writes `CUSTOM_HANDLERS = {}` by mistake, reject cleanly."""
    mod_name = 'hook_manager_test_bad_type_module_xyz'
    fake_module = type(sys)('fake')
    fake_module.CUSTOM_HANDLERS = {'not': 'a list'}  # type: ignore
    sys.modules[mod_name] = fake_module
    try:
        assert _load_custom_handlers(mod_name) == []
    finally:
        del sys.modules[mod_name]


# ── Bad custom_registry degrades gracefully ──────────────────────────

def test_broken_custom_registry_warns_to_stderr(monkeypatch, capsys):
    """A runtime error in custom_registry.py imports a warning but doesn't
    crash the host."""
    import importlib

    def fake_import(name):
        raise RuntimeError('simulated broken custom_registry')

    monkeypatch.setattr(importlib, 'import_module', fake_import)

    out = _load_custom_handlers('hook_manager.custom_registry')
    assert out == []
    captured = capsys.readouterr()
    assert 'custom_registry failed to load' in captured.err
    assert 'simulated broken' in captured.err


