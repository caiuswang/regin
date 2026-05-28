"""Unit tests for lib.logging_setup.

Verifies renderer auto-pick (TTY → console, else JSON), env overrides
(REGIN_LOG_FORMAT / REGIN_LOG_LEVEL), idempotency, and the shared
structlog + stdlib logging pipeline.
"""

from __future__ import annotations

import io
import logging
import sys

from lib import logging_setup


# ── configure_logging idempotency ────────────────────────────

def test_configure_logging_is_idempotent_by_default(monkeypatch):
    monkeypatch.setattr(logging_setup, "_CONFIGURED", False)
    logging_setup.configure_logging()
    first_handlers = list(logging.getLogger().handlers)
    logging_setup.configure_logging()  # no-op
    second_handlers = list(logging.getLogger().handlers)
    assert first_handlers == second_handlers


def test_configure_logging_force_rebuilds(monkeypatch):
    monkeypatch.setattr(logging_setup, "_CONFIGURED", False)
    logging_setup.configure_logging()
    old = list(logging.getLogger().handlers)
    logging_setup.configure_logging(force=True)
    new = list(logging.getLogger().handlers)
    # Force replaces, so identity differs even though the set-count is 1.
    assert new[0] is not old[0]


# ── Renderer picker ──────────────────────────────────────────

def test_pick_renderer_defaults_to_json_when_not_tty(monkeypatch):
    monkeypatch.delenv("REGIN_LOG_FORMAT", raising=False)

    class _NotTTY:
        def isatty(self): return False
    monkeypatch.setattr(sys, "stderr", _NotTTY())
    assert logging_setup._pick_renderer() == "json"


def test_pick_renderer_defaults_to_console_when_tty(monkeypatch):
    monkeypatch.delenv("REGIN_LOG_FORMAT", raising=False)

    class _TTY:
        def isatty(self): return True
    monkeypatch.setattr(sys, "stderr", _TTY())
    assert logging_setup._pick_renderer() == "console"


def test_pick_renderer_env_override_console(monkeypatch):
    monkeypatch.setenv("REGIN_LOG_FORMAT", "console")
    assert logging_setup._pick_renderer() == "console"


def test_pick_renderer_env_override_json(monkeypatch):
    monkeypatch.setenv("REGIN_LOG_FORMAT", "json")
    assert logging_setup._pick_renderer() == "json"


def test_pick_renderer_ignores_bogus_env(monkeypatch):
    monkeypatch.setenv("REGIN_LOG_FORMAT", "yaml")  # not valid

    class _NotTTY:
        def isatty(self): return False
    monkeypatch.setattr(sys, "stderr", _NotTTY())
    # Falls back to TTY-auto path.
    assert logging_setup._pick_renderer() == "json"


# ── Level picker ─────────────────────────────────────────────

def test_pick_level_defaults_to_info(monkeypatch):
    monkeypatch.delenv("REGIN_LOG_LEVEL", raising=False)
    assert logging_setup._pick_level() == logging.INFO


def test_pick_level_env_override(monkeypatch):
    monkeypatch.setenv("REGIN_LOG_LEVEL", "DEBUG")
    assert logging_setup._pick_level() == logging.DEBUG


def test_pick_level_bogus_value_falls_back_to_info(monkeypatch):
    monkeypatch.setenv("REGIN_LOG_LEVEL", "LOUDER")
    assert logging_setup._pick_level() == logging.INFO


# ── get_logger auto-configures ───────────────────────────────

def test_get_logger_auto_configures(monkeypatch):
    monkeypatch.setattr(logging_setup, "_CONFIGURED", False)
    logger = logging_setup.get_logger("test.name")
    # The act of calling get_logger should have flipped _CONFIGURED.
    assert logging_setup._CONFIGURED is True
    # Returned object supports the structlog logger protocol.
    assert hasattr(logger, "info")
    assert hasattr(logger, "warning")


def test_get_logger_without_name_still_works(monkeypatch):
    monkeypatch.setattr(logging_setup, "_CONFIGURED", False)
    logger = logging_setup.get_logger()
    assert hasattr(logger, "info")


# ── End-to-end json emission ─────────────────────────────────

def test_configure_logging_json_mode_emits_json(monkeypatch):
    monkeypatch.setenv("REGIN_LOG_FORMAT", "json")
    monkeypatch.setattr(logging_setup, "_CONFIGURED", False)
    buf = io.StringIO()
    # Redirect the structlog factory output via sys.stderr swap (the
    # module binds PrintLoggerFactory(file=sys.stderr) at configure time).
    monkeypatch.setattr(sys, "stderr", buf)
    logging_setup.configure_logging(force=True)
    log = logging_setup.get_logger("emit-smoke")
    log.info("hello", foo=1)
    text = buf.getvalue()
    # JSON renderer outputs one compact line with event + kwargs.
    assert '"event": "hello"' in text
    assert '"foo": 1' in text
