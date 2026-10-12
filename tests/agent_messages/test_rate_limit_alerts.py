"""F158 限流告警: one warning per (kind, resetsAt, threshold), across sessions."""

from __future__ import annotations

import json

import pytest
from sqlmodel import select
from typer.testing import CliRunner

from cli.app import app
from lib.orm import SessionLocal
from lib.orm.models.agent_messages import AgentMessage, RateLimitAlert

R = "2026-10-12T20:00:00Z"
R2 = "2026-10-13T01:00:00Z"


@pytest.fixture
def pushed(tmp_db, monkeypatch):
    from lib.agent_messages.push import registry as push

    sent: list[dict] = []
    monkeypatch.setattr(push, "maybe_dispatch",
                        lambda msg: sent.append(msg) or None)
    return sent


def _measure(session: str, *windows: dict) -> list[str]:
    stdin = json.dumps({"rateLimits": list(windows), "changed": ["rateLimits"]})
    result = CliRunner().invoke(app, ["usage", "measure", "--session", session],
                                input=stdin)
    assert result.exit_code == 0, result.output
    return result.output.splitlines()


def _messages() -> list[AgentMessage]:
    with SessionLocal() as session:
        return list(session.exec(select(AgentMessage)).all())


def _alerted() -> set[tuple]:
    with SessionLocal() as session:
        return {(r.kind, r.resets_at, r.threshold)
                for r in session.exec(select(RateLimitAlert)).all()}


def test_rate_limit_crossing_raises_one_warning(pushed):
    _measure("A", {"kind": "five_hour", "percentUsed": 82, "resetsAt": R})

    [msg] = _messages()
    assert msg.msg_type == "warning"
    assert msg.title.startswith("five_hour at 82%")
    assert msg.title == f"five_hour at 82% · resets {R}"
    assert msg.trace_id == "A"
    assert [m["msg_type"] for m in pushed] == ["warning"]


def test_the_same_window_and_threshold_alerts_once_across_sessions(pushed):
    _measure("A", {"kind": "five_hour", "percentUsed": 81, "resetsAt": R})
    _measure("B", {"kind": "five_hour", "percentUsed": 85, "resetsAt": R})

    assert len(_messages()) == 1


def test_a_new_reset_window_alerts_again(pushed):
    _measure("A", {"kind": "five_hour", "percentUsed": 81, "resetsAt": R})
    _measure("A", {"kind": "five_hour", "percentUsed": 81, "resetsAt": R2})

    assert [m.title for m in _messages()] == [
        f"five_hour at 81% · resets {R}", f"five_hour at 81% · resets {R2}"]


def test_jumping_past_two_thresholds_alerts_once(pushed):
    _measure("A", {"kind": "five_hour", "percentUsed": 96, "resetsAt": R})

    [msg] = _messages()
    assert msg.title.startswith("five_hour at 96%")
    assert "threshold 95%" in msg.body
    assert _alerted() == {("five_hour", R, 80), ("five_hour", R, 95)}

    _measure("B", {"kind": "five_hour", "percentUsed": 97, "resetsAt": R})
    assert len(_messages()) == 1


def test_a_higher_threshold_in_the_same_window_alerts_once_more(pushed):
    _measure("A", {"kind": "five_hour", "percentUsed": 82, "resetsAt": R})
    _measure("A", {"kind": "five_hour", "percentUsed": 95.5, "resetsAt": R})

    assert [m.title for m in _messages()] == [
        f"five_hour at 82% · resets {R}", f"five_hour at 95.5% · resets {R}"]


def test_windows_without_a_reset_time_never_alert(pushed):
    _measure("A", {"kind": "spend_limit", "percentUsed": 120})

    assert _messages() == []
    assert _alerted() == set()


def test_below_every_threshold_claims_nothing(pushed):
    _measure("A", {"kind": "seven_day", "percentUsed": 79.9, "resetsAt": R})

    assert _messages() == []
    assert _alerted() == set()


def test_thresholds_come_from_settings(pushed, monkeypatch):
    from lib.settings import settings

    monkeypatch.setattr(settings.agent_messages, "rate_limit_alert_at", [50])
    _measure("A", {"kind": "seven_day", "percentUsed": 51, "resetsAt": R})

    assert _alerted() == {("seven_day", R, 50)}


def test_a_malformed_measurement_exits_nonzero(tmp_db):
    result = CliRunner().invoke(app, ["usage", "measure", "--session", "A"],
                                input='{"rateLimits": [{"kind": 1}]}')
    assert result.exit_code == 2
