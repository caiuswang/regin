"""Characterization test: the editable task-run skeletons render exactly the
text the hardcoded f-strings did, plus the two instructions deliberately added
on top of the port (the follow-up reply command, the feature spec rewrite).

The ``_reference_*`` builders below are a **frozen copy** of what
``lib.tasks.runs`` produced. They call the same (unchanged, shared) helpers —
``_invocation``, ``_finish_command``, ``_followup_lines`` — so the only thing
under test is the migrated template bodies plus the context wiring. If the two
ever diverge, an edit dropped or mangled text: change the surface body and this
reference together.
"""

from __future__ import annotations

import pytest

from lib.tasks import run_prompt, runs


def _reference_finish(run_id: int, linked: bool = False,
                      base: str = "") -> str:
    if linked:
        order = (
            "Before you run it: record the work on the linked issue and move "
            "its state — regin re-reads the issue when this command lands, "
            "and a state you change afterwards will not reach the task.\n"
        )
    else:
        target = f"`{base}`" if base else "the branch this run was based on"
        order = (
            f"Before you run it: merge this run's work back into {target} — "
            "that merge is a precondition of reporting finished, not an "
            "optional step, because whatever depends on this task is released "
            "onto that branch and sees only what has landed there.\n"
        )
    return (
        "\n\n<finish>\n"
        "This work was launched from a regin task. Running the command below "
        "is the ONLY thing that marks the task complete and releases whatever "
        "depends on it; your session stays open either way, so run it as soon "
        "as the work is done rather than waiting to be stopped.\n"
        + order
        + f"   {runs._finish_command(run_id)} --reason "
        "'<what you did, and why>'\n"
        "The reason is not bookkeeping: regin posts it on the task as your "
        "report, and it is the only account of this run anybody gets without "
        "reading the whole session. Say what you actually did, what you "
        "decided, and anything you deliberately left — a few sentences, not a "
        "transcript.\n"
        "If you could not finish, run the same command with "
        "`--status failed --reason '<what is blocking it>'`. That report stays "
        "an open thread and is delivered to the next attempt, so write it for "
        "whoever picks this up.\n"
        "</finish>"
    )


def _reference_tracker(name: str, ref: str, url: str) -> str:
    return (
        "\n\n<tracker>\n"
        f"This work is tracked as {name} {ref}. The issue — "
        "and its comments, which can revise or reverse the description — is "
        "the source of truth for what is being asked; regin's copy of the "
        "title and details is a snapshot and may already be stale.\n"
        f"  {url}\n"
        "Read the issue and its comments before you start. When the work "
        "lands, record it there: say what changed, and move the issue's state "
        "to match. Do that before you run the finish command below — regin "
        "re-reads the issue at that moment, so a state you change afterwards "
        "is not the one the feature will show.\n"
        "</tracker>"
    )


def _reference_followup(lines: list[str], dropped: int) -> str:
    """The ported block plus the reply instruction added on top of it."""
    plural = "" if dropped == 1 else "s"
    omitted = (f"\n({dropped} earlier comment{plural} omitted to fit; the "
               "most recent are the ones kept.)\n") if dropped else ""
    return (
        "\n\n<followup>\n"
        "Comments left on this task that nobody has resolved. They were "
        "written on the task page, possibly after an earlier attempt, and "
        "they are still here because the conversation is still open — read "
        "them as part of what is being asked, not as history already dealt "
        "with. Later ones correct earlier ones. They keep arriving on every "
        "run until someone resolves the thread.\n"
        + "".join(lines).lstrip("\n")
        + omitted
        + "Answer them where they are, not only in your final report. For "
        "each thread, say what you did about it and close it:\n"
        "\n"
        f"  {runs._cli_command('task', 'comment', '<thread id>', '--body', '<what you did about it>', '--resolve')}\n"
        "\n"
        "Drop `--resolve` when you are asking something back instead of "
        "settling it. An unresolved thread is delivered again on every later "
        "run, so leaving one open is how you hand a question forward.\n"
        "</followup>"
    )


def _reference_feature(feature_id: int, named: str) -> str:
    """The ported block plus the spec-rewrite instruction added on top."""
    return (
        "\n\n<feature>\n"
        f"This run is a session of feature {named}; regin attributes it "
        "automatically. If you find something the feature got wrong, declare "
        f"it against that id: `regin defects open \"<what>\" --feature "
        f"{feature_id} --session <session id>`. Shipping is the operator's "
        "act on the feature, not yours.\n"
        "The feature's spec is the description this task was cut from, and "
        "this run is what proves it right or wrong. If you finish knowing the "
        "spec now contradicts the code — a decision reversed, a constraint "
        "that turned out false, a piece deliberately not built — rewrite it "
        "rather than leaving the next run to inherit the stale text:\n"
        "\n"
        f"  {runs._cli_command('feature', 'spec', str(feature_id), '--file', '-')}\n"
        "\n"
        "Revise it in place as the design now stands; do not append a "
        "changelog of this run. Leave it alone when the work simply did what "
        "it already said.\n"
        "</feature>"
    )


@pytest.mark.parametrize("linked,base", [(True, ""), (False, ""),
                                         (False, "master")])
def test_finish_block_parity(linked, base):
    assert (runs._finish_block(7, linked=linked, base=base)
            == _reference_finish(7, linked=linked, base=base))


def test_tracker_block_parity():
    class _Feature:
        origin = "linear"
        external_ref = "CAI-223"
        external_url = "https://linear.app/x/issue/CAI-223"

    from lib.tasks.integrations import registry

    feature = _Feature()
    assert (runs._tracker_block(feature)
            == _reference_tracker(registry.display_name(feature.origin),
                                  feature.external_ref,
                                  feature.external_url))


def test_tracker_block_parity_without_url():
    class _Feature:
        origin = "linear"
        external_ref = "CAI-9"
        external_url = None

    from lib.tasks.integrations import registry

    feature = _Feature()
    assert (runs._tracker_block(feature)
            == _reference_tracker(registry.display_name(feature.origin),
                                  feature.external_ref, ""))


@pytest.mark.parametrize("dropped", [0, 1, 3])
def test_followup_block_parity(monkeypatch, dropped):
    lines = ["  [user] first\n", "\n  [agent] second\n"]
    monkeypatch.setattr(run_prompt, "_followup_rows", lambda task_id: [(1, {})])
    monkeypatch.setattr(run_prompt, "_followup_lines",
                        lambda rows: (list(lines), dropped))
    assert runs._followup_block(1) == _reference_followup(lines, dropped)


def test_feature_block_parity(monkeypatch):
    class _Feature:
        title = "Agent SDK runs"

    from lib.features import store as feature_store

    monkeypatch.setattr(feature_store, "get", lambda fid: _Feature())
    assert (runs._feature_block({"feature_id": 80})
            == _reference_feature(80, "#80 (Agent SDK runs)"))


def test_feature_block_parity_unknown_feature(monkeypatch):
    from lib.features import store as feature_store

    monkeypatch.setattr(feature_store, "get", lambda fid: None)
    assert runs._feature_block({"feature_id": 80}) == _reference_feature(80, "#80")


def test_launch_envelope_parity(monkeypatch):
    """The whole prompt, every block present, in the pre-refactor order."""
    monkeypatch.setattr(run_prompt, "_tracker_block", lambda feature: "<T>")
    monkeypatch.setattr(run_prompt, "_followup_block", lambda task_id: "<U>")
    monkeypatch.setattr(run_prompt, "_feature_block", lambda task: "<F>")
    monkeypatch.setattr(run_prompt, "_references_block",
                        lambda task, feature: "<R>")
    monkeypatch.setattr(run_prompt, "_finish_block",
                        lambda run_id, linked=False, base="", task_id=None: "<D>")
    task = {"id": 1, "title": "t", "body": "the idea", "feature_id": None}
    params = {"skill": {"name": "goal-verified", "args": "go"}}
    assert (runs._prompt(task, 7, params)
            == "/goal-verified go\n\nthe idea" + "<T>" + "<U>" + "<F>" + "<R>"
            + "<D>")


def test_launch_envelope_parity_empty_blocks(monkeypatch):
    monkeypatch.setattr(run_prompt, "_tracker_block", lambda feature: "")
    monkeypatch.setattr(run_prompt, "_followup_block", lambda task_id: "")
    monkeypatch.setattr(run_prompt, "_feature_block", lambda task: "")
    monkeypatch.setattr(run_prompt, "_finish_block",
                        lambda run_id, linked=False, base="", task_id=None: "<D>")
    task = {"id": 1, "title": "shelf label", "body": "", "feature_id": None}
    assert runs._prompt(task, 7, None) == "shelf label<D>"


def test_task_body_braces_are_not_re_rendered(monkeypatch):
    """A captured idea containing `{{foo}}` is data, not a placeholder — the
    engine substitutes once and never rescans its own output."""
    monkeypatch.setattr(run_prompt, "_tracker_block", lambda feature: "")
    monkeypatch.setattr(run_prompt, "_followup_block", lambda task_id: "")
    monkeypatch.setattr(run_prompt, "_feature_block", lambda task: "")
    monkeypatch.setattr(run_prompt, "_finish_block",
                        lambda run_id, linked=False, base="", task_id=None: "")
    task = {"id": 1, "title": "t", "body": "use {{finish}} and {literal}",
            "feature_id": None}
    assert runs._prompt(task, 7, None) == "use {{finish}} and {literal}"


# ── the point of the port: the operator can change any of it ──────────────

def test_a_stored_edit_replaces_the_built_in_default(tmp_db):
    """What the whole refactor buys: an edited row is what a run is launched
    with, no code change."""
    from lib.prompt_templates import seed_builtin_skeletons, update_template
    from lib.prompts.surfaces.tasks import FINISH_SURFACE_ID

    seed_builtin_skeletons()
    update_template(FINISH_SURFACE_ID,
                    {"body": "<finish>run {{finish_cmd}}</finish>"})
    block = runs._finish_block(7)
    assert block == f"\n\n<finish>run {runs._finish_command(7)}</finish>"


def test_a_broken_edit_degrades_to_the_default_instead_of_killing_a_run(
        tmp_db):
    """A prompt is user-editable, so a run must survive a bad edit."""
    from lib.prompt_templates import seed_builtin_skeletons, update_template
    from lib.prompts.surfaces.tasks import FINISH_SURFACE_ID

    seed_builtin_skeletons()
    update_template(FINISH_SURFACE_ID, {"body": "{{no_such_variable}}"})
    assert runs._finish_block(7) == _reference_finish(7)


def test_every_task_run_surface_is_seeded_as_a_builtin_row(tmp_db):
    from lib.prompt_templates import list_templates, seed_builtin_skeletons
    from lib.prompts.registry import list_surfaces

    seed_builtin_skeletons()
    seeded = {row["slug"] for row in list_templates() if row["builtin"]}
    registered = {s.id for s in list_surfaces() if s.area == "task-run"}
    assert registered and registered <= seeded


def test_the_ported_bodies_are_registered_as_retired_defaults():
    """The follow-up and feature bodies changed after their first release, so
    an un-edited row seeded from the ported text must heal rather than pin it."""
    import hashlib

    from lib.prompt_templates import _body_sha256
    from lib.prompts.registry import retired_default_hashes
    from lib.prompts.surfaces.tasks import (FEATURE_SURFACE_ID,
                                            FOLLOWUP_SURFACE_ID)

    assert _body_sha256("x") == hashlib.sha256(b"x").hexdigest()
    assert retired_default_hashes(FOLLOWUP_SURFACE_ID)
    assert retired_default_hashes(FEATURE_SURFACE_ID)
