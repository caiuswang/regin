"""Seeds for the reference API: projects (with real git repos), the five DB
kinds, and the three roles that see different things."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sqlmodel import text

from lib.auth import create_token
from lib.orm import SessionLocal
from lib.orm.models.sync import Repo

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}
ADMIN = {"Authorization": f"Bearer {create_token(1, 'ad', 'admin')}"}
#: Overrides the token `flask_client` carries by default. One client per
#: test: two interleaved `with` clients pop each other's app context.
ANON = {"Authorization": ""}

_GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=_GIT_ENV,
                   capture_output=True)


@pytest.fixture
def git_repo(tmp_path):
    """A repo whose `tracked` files are committed and `untracked` ones are
    only on disk."""
    def _make(name: str, tracked=(), untracked=()) -> Path:
        root = tmp_path / name
        root.mkdir()
        _git(root, "init", "-q", "-b", "main")
        for rel in tracked:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(f"{rel}\n")
        if tracked:
            _git(root, "add", "--", *tracked)
            _git(root, "commit", "-q", "-m", "init")
        for rel in untracked:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("not tracked\n")
        return root
    return _make


@pytest.fixture
def project():
    def _make(name: str, path="/nonexistent/regin-test-repo") -> Repo:
        with SessionLocal() as session:
            row = Repo(name=name, path=str(path))
            session.add(row)
            session.commit()
            session.refresh(row)
        return row
    return _make


@pytest.fixture
def feature():
    def _make(title: str, *, body=None, project=None, at=None,
              abandoned=False):
        from lib.features import store as feature_store

        row = feature_store.create(
            title, body=body, project_id=project.id if project else None)
        if abandoned:
            feature_store.abandon(row.id)
        if at is not None:
            # Activity falls back to the events' clock; pin it so "most
            # active" is a fact of the test, not of the second it ran in.
            with SessionLocal() as session:
                session.exec(text(
                    "UPDATE feature_events SET at = :at WHERE feature_id = :f"
                ).bindparams(at=at, f=row.id))
                session.commit()
        return row
    return _make


@pytest.fixture
def task():
    def _make(title: str, *, body=None, project=None, feature_id=None,
              updated="2026-09-01 10:00:00", status=None) -> dict:
        from lib.tasks.store import capture_task

        view, _ = capture_task(
            title=title, source="test", body=body,
            project_id=project.id if project else None,
            feature_id=feature_id)
        with SessionLocal() as session:
            session.exec(text(
                "UPDATE agent_tasks SET updated_at = :u,"
                " status = COALESCE(:s, status) WHERE id = :i"
            ).bindparams(u=updated, s=status, i=view["id"]))
            session.commit()
        return view
    return _make


@pytest.fixture
def defect():
    def _make(symptom: str, *, feature_id=None):
        from lib.defects import store as defect_store

        return defect_store.record(symptom, feature_id=feature_id)
    return _make


@pytest.fixture
def session_trace():
    """A `sessions` row, its repo membership, and its first prompt."""
    def _make(trace_id: str, *, title=None, prompt=None, project=None,
              last_seen="2026-09-01 10:00:00") -> str:
        with SessionLocal() as session:
            session.exec(text(
                "INSERT INTO sessions (trace_id, title, started_at, last_seen)"
                " VALUES (:t, :ti, :s, :s)"
            ).bindparams(t=trace_id, ti=title, s=last_seen))
            if project is not None:
                session.exec(text(
                    "INSERT INTO session_repos (trace_id, repo_id, is_primary)"
                    " VALUES (:t, :r, 1)").bindparams(t=trace_id, r=project.id))
            if prompt is not None:
                session.exec(text(
                    "INSERT INTO session_spans (trace_id, span_id, name,"
                    " start_time, attributes) VALUES (:t, :sp, 'prompt', :s,"
                    " json_object('text', :p))"
                ).bindparams(t=trace_id, sp=f"{trace_id}-p", s=last_seen,
                             p=prompt))
            session.commit()
        return trace_id
    return _make


@pytest.fixture
def lesson():
    def _make(title: str, body: str = "a lesson body", *, project=None) -> str:
        import lib.memory as memory

        scope = f"repo:{project.name}" if project else "global"
        return memory.remember(body, title=title, scope=scope)
    return _make


def groups(body: dict) -> dict:
    return {group["kind"]: group for group in body["groups"]}


def keys(group: dict) -> list[str]:
    return [item["key"] for item in group["items"]]
