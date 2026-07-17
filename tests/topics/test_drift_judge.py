"""Batched drift judge: one LLM pass per sweep judging every pending drift
item with git-diff evidence, falling back to the per-item triage when it
can't run or its answer doesn't parse."""

from __future__ import annotations

import subprocess
from pathlib import Path

from lib.settings import settings
from lib.topics.agent_spawn import maybe_spawn_refresh_agents
from lib.topics.content_drift import detect_drifted_topics, emit_refresh_proposal
from lib.topics.core import write_split_graph
from lib.topics.drift_judge import judge_drift_batch
from lib.topics.proposals import load_proposal
from lib.topics.ref_digest import (
    capture_ref_digests,
    digests_for_topic,
    repo_id_for_path,
)
from lib.topics.snapshots import resolve_or_create_repo
from lib.topics.wiki import wiki_dir


class _StubJudge:
    def __init__(self, answer):
        self._answer = answer
        self.calls = 0

    def complete(self, prompt, *, max_tokens=1024, cwd=None, surface_id=None):
        del max_tokens, cwd, surface_id
        self.calls += 1
        self.last_prompt = prompt
        return self._answer


def _set_judge(monkeypatch, answer) -> _StubJudge:
    judge = _StubJudge(answer)
    monkeypatch.setattr("lib.memory.adapters.resolve_proposal_reviewer",
                        lambda: judge)
    return judge


def _topic(refs: list[dict]) -> dict:
    return {
        "label": "T", "intent": "t", "status": "active", "aliases": [],
        "refs": refs, "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }


def _seed(repo: Path, topics: dict) -> None:
    write_split_graph(repo, {"version": 1, "repo": repo.name,
                            "updated_at": "2026-01-01T00:00:00Z",
                            "topics": topics})
    resolve_or_create_repo(str(repo))


def _commit_all(repo: Path, msg: str) -> None:
    subprocess.check_call(["git", "-C", str(repo), "add", "-A"])
    subprocess.check_call(
        ["git", "-C", str(repo), "commit", "-q", "-m", msg])


def _spy_spawns(monkeypatch) -> list:
    calls: list = []

    def _fake(repo_path, *, run_id=None, topic_request=None, **kw):
        calls.append(run_id)
        return {"run_id": run_id}

    monkeypatch.setattr(
        "lib.topics.proposals.external_jobs.start_external_proposal_run",
        _fake)
    return calls


def _enable_spawn(monkeypatch) -> None:
    monkeypatch.setattr(settings.topic_evolution, "auto_spawn_agents", True)
    monkeypatch.setattr(
        "lib.topics.proposal_external.external_agent_configured",
        lambda: True)


# ── captured_commit + diff evidence ───────────────────────────


def test_capture_stamps_head_commit(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("x\n")
    _seed(repo, {"t1": _topic([{"path": "a.py"}])})
    _commit_all(repo, "baseline")

    capture_ref_digests(repo, "t1")

    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    (digest,) = digests_for_topic(repo_id_for_path(repo), "t1")
    assert digest["captured_commit"] == head


def test_judge_prompt_carries_git_diff(fake_git_repo, monkeypatch):
    repo = fake_git_repo
    (repo / "a.py").write_text("def original_symbol():\n    return 1\n")
    _seed(repo, {"t1": _topic([{"path": "a.py"}])})
    _commit_all(repo, "baseline")
    capture_ref_digests(repo, "t1")
    (repo / "a.py").write_text("def renamed_symbol():\n    return 1\n")
    judge = _set_judge(monkeypatch, "t1: TRIVIAL — rename only")

    verdicts = judge_drift_batch(
        repo, [{"topic_id": "t1", "drifted_paths": ["a.py"],
                "missing_anchors": {}}])

    assert verdicts == {"t1": {"verdict": "trivial",
                               "reason": "rename only"}}
    assert "-def original_symbol" in judge.last_prompt
    assert "+def renamed_symbol" in judge.last_prompt


# ── verdict parsing ───────────────────────────────────────────


def test_parse_ignores_unknown_topics_and_partial_answers(fake_git_repo,
                                                          monkeypatch):
    repo = fake_git_repo
    _seed(repo, {"t1": _topic([]), "t2": _topic([])})
    _set_judge(monkeypatch,
               "preamble chatter\n"
               "t1: MATERIAL — wiki names a deleted command\n"
               "unknown-topic: TRIVIAL — n/a\n")

    verdicts = judge_drift_batch(
        repo, [{"topic_id": "t1", "drifted_paths": []},
               {"topic_id": "t2", "drifted_paths": []}])

    assert verdicts == {"t1": {"verdict": "material",
                               "reason": "wiki names a deleted command"}}
    # t2 absent → the caller fail-opens it to material.


def test_parser_tolerates_llm_decoration(fake_git_repo, monkeypatch):
    """Bullets, bold, backticks, trailing punctuation, a missing em-dash, and
    id case must all parse — every unparsed line costs a fallback triage."""
    repo = fake_git_repo
    _seed(repo, {"t-one": _topic([]), "t.two": _topic([]),
                 "t_three": _topic([]), "tfour": _topic([])})
    _set_judge(monkeypatch,
               "- **t-one**: MATERIAL because the wiki names a gone command\n"
               "* `t.two`: TRIVIAL.\n"
               "T_THREE: **MATERIAL** — case-folded id\n"
               "tfour = TRIVIAL — equals separator\n")

    verdicts = judge_drift_batch(
        repo, [{"topic_id": t, "drifted_paths": []}
               for t in ("t-one", "t.two", "t_three", "tfour")])

    assert verdicts is not None
    assert verdicts["t-one"]["verdict"] == "material"
    assert "gone command" in verdicts["t-one"]["reason"]
    assert verdicts["t.two"]["verdict"] == "trivial"
    assert verdicts["t_three"]["verdict"] == "material"
    assert verdicts["tfour"]["verdict"] == "trivial"


def test_unparseable_answer_returns_none(fake_git_repo, monkeypatch):
    repo = fake_git_repo
    _seed(repo, {"t1": _topic([])})
    _set_judge(monkeypatch, "VERDICT: TRIVIAL")   # old triage format

    assert judge_drift_batch(
        repo, [{"topic_id": "t1", "drifted_paths": []}]) is None


def test_empty_item_list_short_circuits(fake_git_repo, monkeypatch):
    judge = _set_judge(monkeypatch, "t1: TRIVIAL")
    assert judge_drift_batch(fake_git_repo, []) == {}
    assert judge.calls == 0


# ── batched spawn wiring ──────────────────────────────────────


def _seed_two_drifted_stubs(repo: Path) -> None:
    (repo / "a.py").write_text("a\n")
    (repo / "b.py").write_text("b\n")
    _seed(repo, {"t1": _topic([{"path": "a.py"}]),
                 "t2": _topic([{"path": "b.py"}])})
    root = wiki_dir(repo)
    root.mkdir(parents=True, exist_ok=True)
    (root / "t1.md").write_text("wiki one")
    (root / "t2.md").write_text("wiki two")
    _commit_all(repo, "baseline")
    capture_ref_digests(repo, "t1")
    capture_ref_digests(repo, "t2")
    (repo / "a.py").write_text("a changed\n")
    (repo / "b.py").write_text("b changed\n")
    emit_refresh_proposal(repo, "t1", ["a.py"])
    emit_refresh_proposal(repo, "t2", ["b.py"])


def test_one_judge_call_splits_material_from_trivial(fake_git_repo,
                                                     monkeypatch):
    repo = fake_git_repo
    _seed_two_drifted_stubs(repo)
    _enable_spawn(monkeypatch)
    spawns = _spy_spawns(monkeypatch)
    judge = _set_judge(monkeypatch,
                       "t1: MATERIAL — behavior the wiki documents changed\n"
                       "t2: TRIVIAL — formatting only\n")

    spawned = maybe_spawn_refresh_agents(repo)

    assert judge.calls == 1                    # batched: one pass, not two
    assert spawned == 1
    assert spawns == ["content-drift-t1"]
    # the trivial stub was dismissed and its baseline advanced
    proposal = load_proposal(repo, "content-drift-t2")
    assert proposal["topics"][0]["review_status"] == "ignored"
    assert all(d["topic_id"] != "t2" for d in detect_drifted_topics(repo))


def test_batched_trivial_dismisses_origin_run_note(fake_git_repo, monkeypatch):
    """A TRIVIAL verdict from the BATCHED judge must retire an origin-run
    drift note (not just standalone stubs): thread dismissed, baseline
    advanced, no regenerate spawned."""
    import json as _json

    from lib.orm import SessionLocal
    from lib.orm.models import TopicAudit
    from lib.topics.proposal_orm import orm_open_content_drift_threads
    from lib.topics.proposal_orm.runs import orm_save_proposal

    repo = fake_git_repo
    (repo / "a.py").write_text("x\n")
    _seed(repo, {"t1": _topic([{"path": "a.py"}])})
    repo_id = repo_id_for_path(repo)
    orm_save_proposal(str(repo), "origin-1", {
        "provider": "external-agent", "scope": "all", "status": "applied",
        "topics": [{"id": "t1", "label": "T", "aliases": [], "intent": "i",
                    "status": "active", "refs": [], "edges": [],
                    "commands": [], "include_globs": [], "exclude_globs": [],
                    "evidence_paths": []}],
        "metadata": {},
    }, wiki="original narrative")
    with SessionLocal() as s:
        s.add(TopicAudit(
            repo_id=repo_id, kind="provenance",
            recorded_at="2024-01-01T00:00:00Z", severity="info",
            code="topic_create", message="m",
            topic_ids_json=_json.dumps(["t1"]), paths_json="[]",
            aliases_json="[]", triggering_run_id="origin-1",
            triggering_proposal_topic_id=None))
        s.commit()
    capture_ref_digests(repo, "t1")
    (repo / "a.py").write_text("changed\n")
    assert emit_refresh_proposal(repo, "t1", ["a.py"]) == "origin-1"

    _enable_spawn(monkeypatch)
    spawns = _spy_spawns(monkeypatch)
    regens: list = []
    monkeypatch.setattr(
        "lib.topics.proposals.external_jobs.start_external_regenerate_run",
        lambda repo_path, run_id: regens.append(run_id))
    judge = _set_judge(monkeypatch, "t1: TRIVIAL — comment-only churn")

    spawned = maybe_spawn_refresh_agents(repo)

    assert judge.calls == 1
    assert spawned == 0
    assert spawns == [] and regens == []
    assert orm_open_content_drift_threads(
        repo, kind="content_drift", topic_id="t1") == []
    assert detect_drifted_topics(repo) == []   # baseline advanced


def test_judge_failure_falls_back_to_per_item_triage(fake_git_repo,
                                                     monkeypatch):
    repo = fake_git_repo
    _seed_two_drifted_stubs(repo)
    _enable_spawn(monkeypatch)
    spawns = _spy_spawns(monkeypatch)

    class _Flaky:
        calls = 0

        def complete(self, prompt, **kw):
            _Flaky.calls += 1
            if _Flaky.calls == 1:
                raise RuntimeError("judge exploded")
            return "VERDICT: TRIVIAL"           # per-item triage format

    monkeypatch.setattr("lib.memory.adapters.resolve_proposal_reviewer",
                        lambda: _Flaky())

    spawned = maybe_spawn_refresh_agents(repo)

    # batch call failed → each item triaged individually, both TRIVIAL
    assert spawned == 0
    assert spawns == []
    assert _Flaky.calls == 3                    # 1 failed batch + 2 triages
