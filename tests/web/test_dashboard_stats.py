"""Characterization of the `/api/dashboard` stats bundle: skill-state
counts, rule severities merged across engines, and trigger totals."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lib.orm import SessionLocal
from lib.orm.models import RuleTrigger


class _Engine:
    def __init__(self, kind, rules):
        self.kind = kind
        self._rules = rules

    def parse_rules(self):
        return [SimpleNamespace(id=i, severity=s) for i, s in self._rules]


@pytest.fixture
def stubbed(monkeypatch):
    from web.blueprints import meta as meta_bp
    states = [('a', 't', None, None, 'in_sync'), ('b', 't', None, None, 'drifted'),
              ('c', 't', None, None, 'drifted'), ('d', 't', None, None, 'weird')]
    monkeypatch.setattr(meta_bp, "_ss", SimpleNamespace(list_states=lambda: iter(states)))
    monkeypatch.setattr(meta_bp, "load_rules_index", lambda: {"rules": [
        {"id": "g1", "severity": "error"}, {"id": "g2", "severity": "warn"}]})
    monkeypatch.setattr(meta_bp.rule_engines, "all_engines", lambda: [
        _Engine("grit", [("ignored", "error")]),
        _Engine("radon", [("g1", "warn"), ("r1", "error"), ("r1", "error"),
                          ("r2", "info")]),
    ])


def test_dashboard_stats_bundle(flask_client, tmp_db, stubbed):
    with SessionLocal() as session:
        for fired in (1, 0, 1):
            session.add(RuleTrigger(rule_id='x', file_path='f', match_count=fired,
                                    triggered=fired))
        session.commit()
    stats = flask_client.get("/api/dashboard").get_json()["stats"]
    assert stats == {
        "total_repos": 0,
        "total_patterns": 0,
        "total_tags": 0,
        "skills": {"total": 4, "in_sync": 1, "drifted": 2, "source_only": 0,
                   "project_only": 0, "deployed_only": 0},
        "rules": {"total": 4, "error": 2, "warn": 1, "triggers": 3, "fired": 2},
    }
