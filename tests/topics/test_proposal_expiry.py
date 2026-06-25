"""Phase 4a — anti-runaway: expire unreviewed auto-generated proposals.

Auto-provider (content-drift / memory-reflect) proposals left unreviewed past
`auto_proposal_expire_days` are auto-ignored through the real state machine;
human-authored proposals are never touched; the prune is idempotent and folds
into `regin topics evolve`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from lib.settings import settings
from lib.topics.proposal_expiry import expire_stale_auto_proposals
from lib.topics.proposals import load_proposal
from lib.topics.proposal_orm.runs import orm_save_proposal
from lib.topics.snapshots import resolve_or_create_repo

_OLD = "2000-01-01T00:00:00"


def _save(repo, pid, *, provider, topic_id="t1", generated_at,
          status="pending_review"):
    orm_save_proposal(str(repo), pid, {
        "provider": provider, "scope": "all", "status": status,
        "generated_at": generated_at,
        "topics": [{
            "id": topic_id, "label": "T", "aliases": [], "intent": "i",
            "status": "active", "refs": [], "edges": [], "commands": [],
            "include_globs": [], "exclude_globs": [], "evidence_paths": [],
        }],
        "metadata": {},
    }, wiki="w")


def _future(days: int = 100) -> datetime:
    return datetime.now() + timedelta(days=days)


def test_expires_old_unreviewed_auto_proposal(fake_git_repo):
    resolve_or_create_repo(str(fake_git_repo))
    _save(fake_git_repo, "content-drift-t1", provider="content-drift",
          generated_at=_OLD)

    assert expire_stale_auto_proposals(fake_git_repo) == 1
    proposal = load_proposal(fake_git_repo, "content-drift-t1")
    assert proposal["status"] != "pending_review"
    assert proposal["topics"][0]["review_status"] == "ignored"


def test_recent_proposal_is_not_expired(fake_git_repo):
    resolve_or_create_repo(str(fake_git_repo))
    _save(fake_git_repo, "content-drift-t1", provider="content-drift",
          generated_at=datetime.now().isoformat())
    assert expire_stale_auto_proposals(fake_git_repo) == 0


def test_human_proposal_is_never_expired(fake_git_repo):
    resolve_or_create_repo(str(fake_git_repo))
    _save(fake_git_repo, "human-run", provider="claude", generated_at=_OLD)
    assert expire_stale_auto_proposals(fake_git_repo) == 0
    assert load_proposal(fake_git_repo, "human-run")["status"] == "pending_review"


def test_memory_reflect_provider_also_expires(fake_git_repo):
    resolve_or_create_repo(str(fake_git_repo))
    _save(fake_git_repo, "memory-reflect-abc", provider="memory-reflect",
          generated_at=_OLD)
    assert expire_stale_auto_proposals(fake_git_repo) == 1


def test_expiry_is_idempotent(fake_git_repo):
    resolve_or_create_repo(str(fake_git_repo))
    _save(fake_git_repo, "content-drift-t1", provider="content-drift",
          generated_at=_OLD)
    assert expire_stale_auto_proposals(fake_git_repo) == 1
    assert expire_stale_auto_proposals(fake_git_repo) == 0   # already terminal


def test_already_reviewed_proposal_is_not_expired(fake_git_repo):
    resolve_or_create_repo(str(fake_git_repo))
    _save(fake_git_repo, "content-drift-t1", provider="content-drift",
          generated_at=_OLD, status="applied")
    assert expire_stale_auto_proposals(fake_git_repo) == 0


def test_zero_days_disables_expiry(fake_git_repo, monkeypatch):
    monkeypatch.setattr(settings.topic_evolution, "auto_proposal_expire_days", 0)
    resolve_or_create_repo(str(fake_git_repo))
    _save(fake_git_repo, "content-drift-t1", provider="content-drift",
          generated_at=_OLD)
    assert expire_stale_auto_proposals(fake_git_repo) == 0


def test_now_injection_controls_cutoff(fake_git_repo):
    # A proposal generated "now" is expired only when we advance `now` past the
    # window — proving the cutoff math, not just the _OLD sentinel.
    resolve_or_create_repo(str(fake_git_repo))
    _save(fake_git_repo, "content-drift-t1", provider="content-drift",
          generated_at=datetime.now().isoformat())
    assert expire_stale_auto_proposals(fake_git_repo, now=datetime.now()) == 0
    assert expire_stale_auto_proposals(fake_git_repo, now=_future()) == 1


def test_evolve_folds_in_expiry(fake_git_repo, monkeypatch):
    from lib.topics.content_drift import run_content_evolution
    monkeypatch.setattr(settings.topic_evolution, "evolution_enabled", True)
    resolve_or_create_repo(str(fake_git_repo))
    _save(fake_git_repo, "content-drift-old", provider="content-drift",
          topic_id="old", generated_at=_OLD)

    result = run_content_evolution(fake_git_repo)
    assert result["expired"] == 1
