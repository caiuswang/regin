"""The launch surface (`web/blueprints/agent_runs.py`).

The route's job is to start the runner *in this process*, because the parked
question and the route that answers it must share a registry. These tests pin
the refusal behaviour and the in-process contract; `supervisor.launch` is
stubbed so no test spawns a real agent.
"""

from __future__ import annotations

import uuid

import pytest

from lib.agent_sdk import registry, store, supervisor
from lib.settings import settings


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)


@pytest.fixture
def stub_launch(monkeypatch):
    seen = {}

    def _launch(prompt, **kwargs):
        seen["prompt"] = prompt
        seen.update(kwargs)
        return "sdk-deadbeef"

    monkeypatch.setattr(supervisor, "launch", _launch)
    return seen


def test_launch_returns_a_trace_id(flask_client, enabled, stub_launch):
    res = flask_client.post("/api/agent-runs", json={"prompt": "do a thing"})

    assert res.status_code == 200
    assert res.get_json() == {"launched": True, "trace_id": "sdk-deadbeef"}
    assert stub_launch["prompt"] == "do a thing"


def test_disabled_tier_is_a_structured_refusal_not_a_404(flask_client,
                                                         monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "enabled", False)

    res = flask_client.post("/api/agent-runs", json={"prompt": "hi"})

    assert res.status_code == 200
    assert res.get_json() == {"launched": False, "detail": "agent_sdk disabled"}


def test_a_promptless_launch_starts_a_waiting_session(flask_client, enabled,
                                                      stub_launch):
    """Opening the session is itself the act, the way bare `claude` is at a
    terminal — the run comes up on a card with a composer for the first turn."""
    assert flask_client.post("/api/agent-runs",
                             json={"prompt": "   "}).status_code == 200
    assert flask_client.post("/api/agent-runs", json={}).status_code == 200
    assert stub_launch["prompt"] == ""


def test_a_promptless_one_shot_is_rejected(flask_client, enabled, stub_launch):
    """It would connect and immediately disconnect: the turn it was told to end
    with was never given."""
    res = flask_client.post("/api/agent-runs", json={"one_shot": True})

    assert res.status_code == 400
    assert "prompt" not in stub_launch


def test_prompt_is_bounded(flask_client, enabled, stub_launch):
    flask_client.post("/api/agent-runs", json={"prompt": "x" * 20000})

    assert len(stub_launch["prompt"]) == 8000


def test_capacity_refusal_is_surfaced(flask_client, enabled, monkeypatch):
    def _busy(prompt, **kwargs):
        raise supervisor.LaunchRefused("max_concurrent_runs reached")

    monkeypatch.setattr(supervisor, "launch", _busy)

    body = flask_client.post("/api/agent-runs", json={"prompt": "hi"}).get_json()

    assert body == {"launched": False, "detail": "max_concurrent_runs reached"}


def test_an_id_already_claimed_by_another_launch_is_a_structured_refusal(
        flask_client, enabled, monkeypatch):
    """The narrow-window twin of `_resume_target`'s "that run is still live":
    the id was taken by a launch already under way. An operator gets a
    sentence in the sheet, not a 500."""
    class _FixedUuid:
        @staticmethod
        def uuid4():
            return uuid.UUID(int=0xC0FFEE)

    monkeypatch.setattr(supervisor, "uuid", _FixedUuid)
    minted = f"sdk-{uuid.UUID(int=0xC0FFEE).hex[:12]}"
    registry.reserve_run(minted)
    try:
        res = flask_client.post("/api/agent-runs", json={"prompt": "hi"})
    finally:
        registry.release_run(minted)

    assert res.status_code == 200
    assert res.get_json() == {"launched": False,
                              "detail": "that run is already starting"}


def test_missing_sdk_package_explains_the_extra(flask_client, enabled,
                                                monkeypatch):
    def _missing(prompt, **kwargs):
        raise ImportError("no module named claude_agent_sdk")

    monkeypatch.setattr(supervisor, "launch", _missing)

    body = flask_client.post("/api/agent-runs", json={"prompt": "hi"}).get_json()

    assert body["launched"] is False
    assert "agent-sdk" in body["detail"]


def test_status_reports_reachability_separately_from_stored_status(flask_client):
    store.upsert_run("sdk-status-test", status="running")
    try:
        body = flask_client.get("/api/agent-runs/sdk-status-test").get_json()

        assert body["status"] == "running"
        # No live runner in this process, so the row's claim is not reachability.
        assert body["owned"] is False

        registry.register_run("sdk-status-test", object())
        assert flask_client.get(
            "/api/agent-runs/sdk-status-test").get_json()["owned"] is True
    finally:
        registry.unregister_run("sdk-status-test")


def test_unknown_run_is_404(flask_client):
    assert flask_client.get("/api/agent-runs/nope").status_code == 404


# ── per-run overrides ────────────────────────────────────────────────

def test_per_run_overrides_reach_the_supervisor(flask_client, enabled,
                                                stub_launch):
    res = flask_client.post("/api/agent-runs", json={
        "prompt": "go", "cwd": "/tmp/x", "model": "claude-opus-5",
        "permission_mode": "plan", "one_shot": True, "resume": "abc-123",
    })

    assert res.status_code == 200
    assert stub_launch["cwd"] == "/tmp/x"
    assert stub_launch["model"] == "claude-opus-5"
    assert stub_launch["permission_mode"] == "plan"
    assert stub_launch["one_shot"] is True
    assert stub_launch["resume"] == "abc-123"


def test_unknown_permission_mode_is_a_400_not_a_dead_run(flask_client, enabled,
                                                        stub_launch):
    res = flask_client.post("/api/agent-runs",
                            json={"prompt": "go", "permission_mode": "yolo"})

    assert res.status_code == 400
    # Refused before launching: an unknown mode reaching the SDK would surface
    # as a run that died on start.
    assert "prompt" not in stub_launch


def test_resuming_a_run_regin_never_recorded_is_refused(flask_client, enabled,
                                                        stub_launch):
    """`sdk-…` is regin's name for a run, not a session the CLI can reopen — so
    with no row naming the child behind it there is nothing to resume. Reviving
    a run regin DOES have a row for is `test_resume_run.py`."""
    res = flask_client.post("/api/agent-runs",
                            json={"prompt": "go", "resume": "sdk-abc123"})

    assert res.status_code == 400
    assert "prompt" not in stub_launch


def test_omitted_overrides_stay_empty(flask_client, enabled, stub_launch):
    flask_client.post("/api/agent-runs", json={"prompt": "go"})

    assert stub_launch["cwd"] is None
    assert stub_launch["resume"] is None
    assert stub_launch["model"] == ""
    assert stub_launch["permission_mode"] == ""
    assert stub_launch["one_shot"] is False


def test_a_mode_that_shadows_gating_is_reported_on_the_launch(
        flask_client, enabled, stub_launch, monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "gate_plan", True)

    body = flask_client.post("/api/agent-runs", json={
        "prompt": "go", "permission_mode": "acceptEdits",
    }).get_json()

    assert body["launched"] is True
    assert "acceptEdits" in body["warning"]


def test_no_warning_when_nothing_is_gated(flask_client, enabled, stub_launch,
                                          monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "gate_plan", False)
    monkeypatch.setattr(settings.agent_sdk, "gated_tools", [])

    body = flask_client.post("/api/agent-runs", json={
        "prompt": "go", "permission_mode": "acceptEdits",
    }).get_json()

    assert body == {"launched": True, "trace_id": "sdk-deadbeef"}


# ── launch options ───────────────────────────────────────────────────

def test_launch_options_describe_this_install(flask_client, enabled,
                                              monkeypatch):
    from pathlib import Path
    # Real `repo_paths` are `Path`s — serialize them, or the route 500s on
    # every install that has registered a repo.
    monkeypatch.setattr(settings, "repo_paths", [Path("/repo/a"),
                                                Path("/repo/b")])
    monkeypatch.setattr(settings.agent_sdk, "permission_mode", "plan")

    body = flask_client.get("/api/agent-runs/launch-options").get_json()

    assert body["enabled"] is True
    assert body["cwds"] == ["/repo/a", "/repo/b"]
    assert body["default_permission_mode"] == "plan"
    assert "bypassPermissions" in body["permission_modes"]


def test_launch_options_report_a_disabled_tier(flask_client, monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "enabled", False)

    assert flask_client.get(
        "/api/agent-runs/launch-options").get_json()["enabled"] is False


# ── model + effort are picked per run ─────────────────────────────────


def test_launch_options_offer_models_and_efforts(flask_client, enabled,
                                                 monkeypatch):
    """Served rather than hardcoded in the client, for the same reason the
    permission modes are: the menu describes the install this browser drives."""
    monkeypatch.setattr(settings.agent_sdk, "model", "sonnet")
    monkeypatch.setattr(settings.agent_sdk, "effort", "high")

    body = flask_client.get("/api/agent-runs/launch-options").get_json()

    assert "opus" in body["models"]
    assert body["efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert body["default_model"] == "sonnet"
    assert body["default_effort"] == "high"


def test_a_picked_effort_reaches_the_launch(flask_client, enabled, stub_launch):
    res = flask_client.post("/api/agent-runs",
                            json={"prompt": "hi", "effort": "xhigh"})

    assert res.status_code == 200
    assert stub_launch["effort"] == "xhigh"


def test_an_unknown_effort_is_a_bad_request(flask_client, enabled, stub_launch):
    """Checked here rather than left to the SDK: an unknown level reaching the
    launch surfaces as a run that died on start, which reads like regin broke."""
    res = flask_client.post("/api/agent-runs",
                            json={"prompt": "hi", "effort": "ludicrous"})

    assert res.status_code == 400
    assert "ludicrous" in res.get_json()["error"]
    assert stub_launch == {}


def test_no_effort_defers_to_the_install(flask_client, enabled, stub_launch):
    flask_client.post("/api/agent-runs", json={"prompt": "hi"})

    assert stub_launch["effort"] == ""


def test_a_model_outside_the_menu_is_still_accepted(flask_client, enabled,
                                                    stub_launch):
    """The menu decides what is one tap away, NOT what is legal — an operator
    pinning an exact id must stay able to."""
    flask_client.post("/api/agent-runs",
                      json={"prompt": "hi", "model": "claude-opus-4-8"})

    assert stub_launch["model"] == "claude-opus-4-8"


# ── Launching into a git worktree ────────────────────────────────────────────
# `ensure_worktree` is stubbed here: what these pin is the route's wiring — that
# the created path becomes the run's cwd, that a refusal is a 400, and that the
# tree is reported even when the launch behind it is not. Real git behaviour
# lives in `test_worktree.py`, including one end-to-end pass through this route.


@pytest.fixture
def stub_worktree(monkeypatch):
    from lib.agent_sdk import worktree

    asked = {}

    def _ensure(cwd, branch, base=""):
        asked.update(cwd=cwd, branch=branch, base=base)
        return {"path": "/tmp/proj/.claude/worktrees/feat-x", "branch": branch,
                "repo_root": "/tmp/proj", "created": True}

    monkeypatch.setattr(worktree, "ensure_worktree", _ensure)
    return asked


def test_a_worktree_launch_runs_in_the_tree_it_just_made(flask_client, enabled,
                                                         stub_launch,
                                                         stub_worktree):
    res = flask_client.post("/api/agent-runs", json={
        "prompt": "hi", "cwd": "/tmp/proj",
        "worktree": {"branch": "feat/x", "base": "main"},
    })

    assert res.status_code == 200
    assert stub_launch["cwd"] == "/tmp/proj/.claude/worktrees/feat-x"
    assert stub_worktree == {"cwd": "/tmp/proj", "branch": "feat/x",
                             "base": "main"}
    assert res.get_json()["worktree"]["path"] == stub_launch["cwd"]


def test_a_worktree_with_no_branch_named_is_not_a_worktree_request(
        flask_client, enabled, stub_launch, stub_worktree):
    """The branch IS the ask — a blank one is an untouched form, not an error."""
    res = flask_client.post("/api/agent-runs",
                            json={"prompt": "hi", "cwd": "/tmp/proj",
                                  "worktree": {"branch": "  "}})

    assert res.status_code == 200
    assert stub_launch["cwd"] == "/tmp/proj"
    assert stub_worktree == {}
    assert "worktree" not in res.get_json()


def test_an_over_long_branch_is_refused_rather_than_clipped(flask_client,
                                                            enabled,
                                                            stub_launch,
                                                            stub_worktree):
    """The branch is created under the name given, so clipping it the way the
    other identifiers are clipped would make a branch nobody asked for."""
    res = flask_client.post("/api/agent-runs", json={
        "prompt": "hi", "worktree": {"branch": "f" * 201},
    })

    assert res.status_code == 400
    assert "longer than" in res.get_json()["error"]
    assert stub_worktree == {}
    assert stub_launch == {}


def test_a_resume_cannot_be_moved_into_a_new_worktree(flask_client, enabled,
                                                      stub_launch,
                                                      stub_worktree):
    """`claude --resume` resolves the id against the working directory, so a
    continuation started in a fresh tree would silently be a new session."""
    res = flask_client.post("/api/agent-runs", json={
        "resume": str(uuid.uuid4()), "worktree": {"branch": "feat/x"},
    })

    assert res.status_code == 400
    assert "worktree" in res.get_json()["error"]
    assert stub_worktree == {}
    assert stub_launch == {}


def test_a_refused_worktree_is_a_bad_request_with_the_reason(flask_client,
                                                             enabled,
                                                             stub_launch,
                                                             monkeypatch):
    from lib.agent_sdk import worktree

    def _refuse(cwd, branch, base=""):
        raise worktree.WorktreeRefused("unknown base ref 'nope'")

    monkeypatch.setattr(worktree, "ensure_worktree", _refuse)

    res = flask_client.post("/api/agent-runs",
                            json={"prompt": "hi",
                                  "worktree": {"branch": "feat/x",
                                               "base": "nope"}})

    assert res.status_code == 400
    assert res.get_json()["error"] == "unknown base ref 'nope'"
    assert stub_launch == {}


def test_a_refused_launch_still_names_the_tree_it_left_behind(flask_client,
                                                              enabled,
                                                              stub_worktree,
                                                              monkeypatch):
    """The directory is on disk by the time the launch is refused; an operator
    told only "refused" would go looking for something nothing mentioned."""
    def _refuse(prompt, **kwargs):
        raise supervisor.LaunchRefused("at capacity")

    monkeypatch.setattr(supervisor, "launch", _refuse)

    body = flask_client.post("/api/agent-runs",
                             json={"prompt": "hi",
                                   "worktree": {"branch": "feat/x"}}).get_json()

    assert body["launched"] is False
    assert body["worktree"]["path"] == "/tmp/proj/.claude/worktrees/feat-x"


def test_worktree_options_serve_the_checkouts_own_branches(flask_client,
                                                           enabled,
                                                           monkeypatch):
    from lib.agent_sdk import worktree

    monkeypatch.setattr(
        worktree, "options",
        lambda cwd: {"repo_root": cwd, "branches": ["main"],
                     "default_base": "main",
                     "worktrees_root": f"{cwd}/.claude/worktrees"})

    res = flask_client.get("/api/agent-runs/worktree-options?cwd=/tmp/proj")

    assert res.status_code == 200
    assert res.get_json()["repo_root"] == "/tmp/proj"


def test_worktree_options_on_a_non_checkout_say_so(flask_client, enabled,
                                                   monkeypatch):
    """A 400 with the reason, so the form disables its worktree section rather
    than offering branches it invented."""
    from lib.agent_sdk import worktree

    def _refuse(cwd):
        raise worktree.WorktreeRefused(f"{cwd} is not a normal git checkout")

    monkeypatch.setattr(worktree, "options", _refuse)

    res = flask_client.get("/api/agent-runs/worktree-options?cwd=/tmp/plain")

    assert res.status_code == 400
    assert "not a normal git checkout" in res.get_json()["error"]
