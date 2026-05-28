"""Config-only language→extension overrides for the PostToolUse rule gate.

`settings.language_extensions` lets an operator route a rule engine to a
brand-new language with no code change. It is consulted before the
`lib.languages` registry and the handler's built-in `_FALLBACK_EXTENSIONS`
map, so it can introduce a new language id or override a known one.
"""

from __future__ import annotations

from hook_manager.handlers import rule_check
from lib import settings as settings_mod


class _StubEngine:
    kind = "grit"

    def __init__(self, engine_id, language_ids):
        self.id = engine_id
        self.language_ids = tuple(language_ids)


def test_unknown_language_has_no_extensions_without_config(monkeypatch):
    monkeypatch.setattr(settings_mod.settings, "language_extensions", {})
    assert rule_check._extensions_for_language("kotlin", None) == ()


def test_config_declares_extensions_for_new_language(monkeypatch):
    monkeypatch.setattr(
        settings_mod.settings, "language_extensions", {"kotlin": [".kt", ".kts"]}
    )
    assert rule_check._extensions_for_language("kotlin", None) == (".kt", ".kts")


def test_config_overrides_builtin_fallback(monkeypatch):
    # 'go' ships in _FALLBACK_EXTENSIONS as ('.go',); the config wins.
    monkeypatch.setattr(
        settings_mod.settings, "language_extensions", {"go": [".gogo"]}
    )
    assert rule_check._extensions_for_language("go", None) == (".gogo",)


def test_engine_routed_to_config_declared_language(monkeypatch):
    monkeypatch.setattr(
        settings_mod.settings, "language_extensions", {"kotlin": [".kt"]}
    )
    engine = _StubEngine("grit", ["kotlin"])
    monkeypatch.setattr(rule_check.rule_engines, "all_engines", lambda: [engine])

    assert rule_check._engines_for_file("/tmp/Foo.kt", None) == [(engine, "kotlin")]
    # A non-matching extension routes nowhere.
    assert rule_check._engines_for_file("/tmp/Foo.py", None) == []
