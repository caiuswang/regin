"""Scope resolution for the *deliberate* retrieval front-ends.

`resolve_recall_scope` is the hook's auto-injection policy; `resolve_tool_scope`
is what the `recall` / `index_*` tools and CLI commands apply, so a skill's own
recall obeys the same policy as the injected `<recalled_experience>` block.
"""

from __future__ import annotations

from lib.memory.scoping import ALL_SCOPES, resolve_tool_scope
from lib.settings import settings


def _registered_repo(monkeypatch, tmp_path):
    repo = tmp_path / "myrepo"
    (repo / "src").mkdir(parents=True)
    monkeypatch.setattr(settings, "repo_paths", [repo])
    return repo


def test_empty_request_applies_per_repo_policy(monkeypatch, tmp_path):
    repo = _registered_repo(monkeypatch, tmp_path)
    monkeypatch.setattr(settings.agent_memory, "scope_policy", "per-repo")
    assert resolve_tool_scope("", str(repo / "src")) == "repo:myrepo"
    assert resolve_tool_scope(None, str(repo / "src")) == "repo:myrepo"


def test_empty_request_is_unscoped_under_the_default_policy(monkeypatch,
                                                            tmp_path):
    repo = _registered_repo(monkeypatch, tmp_path)
    # per-repo-tagged records the scope but never filters on it.
    monkeypatch.setattr(settings.agent_memory, "scope_policy",
                        "per-repo-tagged")
    assert resolve_tool_scope("", str(repo / "src")) is None


def test_explicit_scope_beats_the_policy(monkeypatch, tmp_path):
    repo = _registered_repo(monkeypatch, tmp_path)
    monkeypatch.setattr(settings.agent_memory, "scope_policy", "per-repo")
    assert resolve_tool_scope("repo:other", str(repo / "src")) == "repo:other"
    assert resolve_tool_scope("global", str(repo / "src")) == "global"


def test_all_opts_out_of_scoping(monkeypatch, tmp_path):
    repo = _registered_repo(monkeypatch, tmp_path)
    monkeypatch.setattr(settings.agent_memory, "scope_policy", "per-repo")
    assert resolve_tool_scope(ALL_SCOPES, str(repo / "src")) is None
    assert resolve_tool_scope("all", str(repo / "src")) is None


def test_unregistered_cwd_stays_unscoped(monkeypatch, tmp_path):
    _registered_repo(monkeypatch, tmp_path)
    monkeypatch.setattr(settings.agent_memory, "scope_policy", "per-repo")
    # Outside every registered repo there is no repo name to narrow to; the
    # caller sees the whole store rather than an empty result.
    assert resolve_tool_scope("", str(tmp_path / "elsewhere")) is None
