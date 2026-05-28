"""Unit tests for lib.tokens.model_windows."""

from __future__ import annotations

from lib.tokens.model_windows import DEFAULT_WINDOW, infer_window, window_for


# ── window_for: per-model lookup ────────────────────────────────

def test_opus_4_7_is_1m_native():
    # Opus 4.7 ships with a 1M-token native window per Anthropic docs.
    assert window_for("claude-opus-4-7") == 1_000_000


def test_sonnet_4_6_is_1m_native():
    assert window_for("claude-sonnet-4-6") == 1_000_000


def test_haiku_4_5_is_200k():
    assert window_for("claude-haiku-4-5") == 200_000
    assert window_for("claude-haiku-4-5-20251001") == 200_000


def test_legacy_1m_suffix_alias_still_resolves():
    # Older Claude Code transcripts tagged the extended-context variant
    # with `[1m]`; the alias is kept for back-compat.
    assert window_for("claude-opus-4-7[1m]") == 1_000_000


def test_unknown_model_returns_default():
    assert window_for("made-up-model") == DEFAULT_WINDOW


def test_none_returns_default():
    assert window_for(None) == DEFAULT_WINDOW


def test_empty_string_returns_default():
    assert window_for("") == DEFAULT_WINDOW


def test_unknown_dated_alias_strips_to_base():
    # `claude-opus-4-7-20260101` should fall back to `claude-opus-4-7`.
    assert window_for("claude-opus-4-7-20260101") == 1_000_000


# ── infer_window: cap behavior ──────────────────────────────────

def test_infer_window_returns_known_window_when_peak_fits():
    assert infer_window("claude-opus-4-7", 100_000) == 1_000_000


def test_infer_window_does_not_grow_past_known_window():
    # Even if peak somehow exceeds a known model's window, we trust the
    # configured value over the observation — no silent inflation.
    assert infer_window("claude-haiku-4-5", 250_000) == 200_000


def test_infer_window_falls_back_to_peak_for_unknown_family():
    # Truly unknown model — keep peak as the cap so frontend % doesn't
    # divide by zero.
    assert infer_window("unknown-model", 500_000) == 500_000


def test_infer_window_none_model():
    assert infer_window(None, 100_000) == DEFAULT_WINDOW


# ── settings override ───────────────────────────────────────────

def test_settings_override_extends_table(monkeypatch):
    from lib.settings import settings
    monkeypatch.setattr(
        settings, "model_context_windows", {"custom-model-x": 500_000}, raising=False
    )
    assert window_for("custom-model-x") == 500_000


def test_settings_override_can_replace_builtin(monkeypatch):
    from lib.settings import settings
    monkeypatch.setattr(
        settings, "model_context_windows", {"claude-haiku-4-5": 400_000}, raising=False
    )
    assert window_for("claude-haiku-4-5") == 400_000
