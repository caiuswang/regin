"""Seeding helpers for the palette's home and relations tests."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pytest


def ago(seconds: float) -> str:
    return (datetime.now() - timedelta(seconds=seconds)).isoformat()


class Seed:
    def __init__(self, conn):
        self.conn = conn

    def session(self, trace_id, *, title='t', status='active', last_seen=None):
        self.conn.execute(
            "INSERT INTO sessions (trace_id, title, title_source, status, started_at, "
            " last_seen, cwd) VALUES (?, ?, 'user', ?, '2026-10-01T00:00:00', ?, '/tmp/r')",
            (trace_id, title, status, last_seen or ago(5)))

    def span(self, trace_id, span_id, name, *, start, status='OK', attrs=None):
        self.conn.execute(
            "INSERT INTO session_spans (trace_id, span_id, name, kind, start_time, "
            " status_code, attributes) VALUES (?, ?, ?, 'internal', ?, ?, ?)",
            (trace_id, span_id, name, start, status, json.dumps(attrs or {})))

    def parked(self, trace_id, *, seconds_ago, attrs=None, name='permission.request'):
        """A session whose newest span is a PENDING human blocker."""
        self.session(trace_id)
        self.span(trace_id, f'{trace_id}-p', 'prompt', start=ago(seconds_ago + 60),
                  attrs={'text': 'go'})
        self.span(trace_id, f'permreq-{trace_id}', name, start=ago(seconds_ago),
                  status='PENDING', attrs=attrs or {'tool_name': 'Bash',
                                                    'command_preview': 'ls'})

    def working(self, trace_id):
        self.session(trace_id, last_seen=ago(1))
        self.span(trace_id, f'{trace_id}-w', 'tool.Bash', start=ago(2))

    def feature(self, fid, title='F', *, building=False):
        self.conn.execute("INSERT INTO features (id, title, body) VALUES (?, ?, ?)",
                          (fid, title, f'{title} summary'))
        if building:
            self.conn.execute("INSERT INTO feature_events (feature_id, kind, declared_by) "
                              "VALUES (?, 'build_started', 'test')", (fid,))

    def task(self, tid, title='T', *, status='running', feature_id=None):
        self.conn.execute(
            "INSERT INTO agent_tasks (id, title, body, status, source, feature_id) "
            "VALUES (?, ?, ?, ?, 'native', ?)",
            (tid, title, f'{title} body', status, feature_id))

    def run(self, task_id, trace_id, *, attempt=1, status='running'):
        self.conn.execute(
            "INSERT INTO agent_task_runs (task_id, trace_id, attempt, status) "
            "VALUES (?, ?, ?, ?)", (task_id, trace_id, attempt, status))

    def alias(self, run_id, child_id):
        self.conn.execute("INSERT INTO agent_runs (trace_id, cli_session_id) VALUES (?, ?)",
                          (run_id, child_id))

    def link(self, trace_id, feature_id, *, primary=1):
        self.conn.execute(
            "INSERT INTO session_features (trace_id, feature_id, is_primary) "
            "VALUES (?, ?, ?)", (trace_id, feature_id, primary))

    def defect(self, did, *, feature_id=None, status='open', triage=None,
               opened_at='2026-10-01 00:00:00', reporter=None, fix=None,
               symptom=None, snoozed_held=False):
        self.conn.execute(
            "INSERT INTO defects (id, feature_id, symptom, symptom_key, status, "
            " triage_state, opened_at, reported_by_trace, fix_task_id, "
            " snooze_watermark, activity_seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (did, feature_id, symptom or f'symptom {did}', f'k{did}', status, triage,
             opened_at, reporter, fix, 0 if snoozed_held else None))


@pytest.fixture
def conn(tmp_db):
    c = sqlite3.connect(str(tmp_db))
    yield c
    c.close()


@pytest.fixture
def seed(conn):
    return Seed(conn)

