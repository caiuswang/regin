"""A run is handed what its task and its feature's spec cite (F131 R7).

Each reference is resolved at launch and listed on one line with the command
that reads it in full, rather than copied into the prompt: a copy drifts, and a
pointer costs one line and is current when the run starts.
"""

from __future__ import annotations

import shlex
from types import SimpleNamespace

import pytest
from sqlmodel import select, text
from typer.testing import CliRunner

from cli.app import app
from lib.defects import store as defect_store
from lib.features import store as feature_store
from lib.orm import SessionLocal
from lib.orm.models import PromptTemplate
from lib.orm.models.sync import Repo
from lib.prompt_templates import (get_template_by_slug, seed_builtin_skeletons,
                                  update_template)
from lib.prompts.surfaces.tasks import LAUNCH_SURFACE_ID
from lib.tasks import runs
from lib.tasks.run_prompt import (PROMPT_MAX, REFERENCES_MAX, _cli_command,
                                  _reference_line)
from lib.tasks.store import capture_task

# The default envelope as it shipped before it carried `{{references}}` — what
# an install that never edited it still stores, byte for byte.
_PREVIOUS_LAUNCH_DEFAULT = (
    "{{invocation}}{{body}}{{tracker}}{{followup}}{{feature}}{{spec}}"
    "{{finish}}"
)


@pytest.fixture
def launcher(monkeypatch):
    state = SimpleNamespace(prompts=[])

    def _fake(prompt, **kwargs):
        state.prompts.append(prompt)
        return SimpleNamespace(trace_id=f"sdk-fake{len(state.prompts)}",
                               add_done_callback=lambda callback: None)

    monkeypatch.setattr(runs.supervisor, "launch_run", _fake)
    return state


@pytest.fixture
def run(flask_client, launcher):
    """Arm a task and run it through the route; the prompt it launched with."""
    def _run(task_id: int) -> str:
        assert flask_client.post(f"/api/tasks/{task_id}/arm",
                                 json={}).status_code == 200
        resp = flask_client.post(f"/api/tasks/{task_id}/run")
        assert resp.status_code == 201, resp.get_json()
        return launcher.prompts[-1]
    return _run


def _task(body: str, **fields) -> int:
    view, _ = capture_task(title="shelf label", source="test", body=body,
                           **fields)
    return view["id"]


def _project(name: str) -> Repo:
    with SessionLocal() as session:
        row = Repo(name=name, path="/nonexistent/regin-test-repo")
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def _lines(prompt: str) -> list[str]:
    """The references block's lines, without its tags or its preamble."""
    assert prompt.count("<references>") == 1, prompt
    inner = prompt.split("<references>\n", 1)[1].split("\n</references>")[0]
    return [line for line in inner.split("\n")
            if line.startswith(("- ", "… "))]


def _stage(feature_id: int) -> str:
    from lib.refs.resolve import resolve_refs

    return resolve_refs([f"feature:{feature_id}"],
                        can_read_sessions=True)[f"feature:{feature_id}"][
                            "status"]


def test_run_prompt_lists_references_with_read_commands(run):
    alpha = feature_store.create("Alpha")
    delta = defect_store.record("Delta broke", feature_id=alpha.id)
    gamma = feature_store.create(
        "Gamma", body=f"Builds on [F{alpha.id} Alpha](feature:{alpha.id}).")
    task_id = _task(f"Fix [D{delta.id} Delta broke](defect:{delta.id}) first.",
                    feature_id=gamma.id)

    prompt = run(task_id)

    assert _lines(prompt) == [
        f"- D{delta.id} 「Delta broke」 — defect, status open, "
        f"feature #{alpha.id}",
        f"- F{alpha.id} 「Alpha」 — feature, stage {_stage(alpha.id)} — read: "
        + _cli_command("feature", "spec", str(alpha.id)),
    ]
    assert f"feature spec {alpha.id}" in _lines(prompt)[1]
    assert (prompt.index("</feature>") < prompt.index("<references>")
            < prompt.index("</references>") < prompt.index("<finish>"))


@pytest.mark.parametrize("body", [
    "Wire the thing.",
    "Only in code: `[F1 x](feature:1)`.",
])
def test_run_prompt_without_references_has_no_block(run, body):
    quiet = feature_store.create(
        "Quiet", body="```\n[F1 x](feature:1)\n```\nNothing cited here.")
    task_id = _task(body, feature_id=quiet.id)

    prompt = run(task_id)

    assert "<references>" not in prompt
    assert "</references>" not in prompt
    assert "looked up when this run was launched" not in prompt
    assert "</feature>\n\n<finish>" in prompt


def test_references_past_the_body_cut_are_still_listed(run):
    alpha = feature_store.create("Alpha")
    link = f"[F{alpha.id} Alpha](feature:{alpha.id})"
    body = "x" * (PROMPT_MAX + 100) + link
    body += "y" * (PROMPT_MAX + 500 - len(body))
    assert len(body) == PROMPT_MAX + 500 and body.index(link) > PROMPT_MAX

    prompt = run(_task(body))

    assert prompt[:PROMPT_MAX] == body[:PROMPT_MAX]
    assert prompt[PROMPT_MAX:].startswith("\n\n<references>\n")
    assert link not in prompt
    assert _lines(prompt) == [
        f"- F{alpha.id} 「Alpha」 — feature, stage {_stage(alpha.id)} — read: "
        + _cli_command("feature", "spec", str(alpha.id))]


def _write_body_verbatim(slug: str, body: str) -> None:
    """The stored body byte for byte, as the seeder wrote it — `update_template`
    strips, so it cannot reproduce a seeded row."""
    with SessionLocal() as session:
        row = session.exec(select(PromptTemplate)
                           .where(PromptTemplate.slug == slug)).one()
        row.body = body
        session.add(row)
        session.commit()


def test_unedited_launch_envelope_upgrades_to_carry_references():
    seed_builtin_skeletons()
    _write_body_verbatim(LAUNCH_SURFACE_ID, _PREVIOUS_LAUNCH_DEFAULT)

    assert seed_builtin_skeletons() == 1

    row = get_template_by_slug(LAUNCH_SURFACE_ID)
    assert "{{references}}" in row["body"]
    assert row["body"].endswith("{{spec}}{{references}}{{finish}}")
    assert "references" in [var["name"] for var in row["variables"]]
    assert seed_builtin_skeletons() == 0


def test_an_envelope_edited_without_references_carries_no_block(run):
    """The block is the operator's to remove. Only `{{finish}}` is put back,
    because only it lets the task complete."""
    alpha = feature_store.create("Alpha")
    seed_builtin_skeletons()
    update_template(LAUNCH_SURFACE_ID, {"body": "{{body}}{{finish}}"})

    prompt = run(_task(f"See [F{alpha.id} Alpha](feature:{alpha.id})."))

    assert "<references>" not in prompt
    assert prompt.count("<finish>") == 1


def _session(trace_id: str, title: str) -> None:
    with SessionLocal() as session:
        session.exec(text(
            "INSERT INTO sessions (trace_id, title, started_at, last_seen)"
            " VALUES (:t, :ti, '2026-09-01 10:00:00', '2026-09-01 10:00:00')"
        ).bindparams(t=trace_id, ti=title))
        session.commit()


def test_every_kind_has_its_line_and_its_read_command_runs(run):
    import lib.memory as memory

    project = _project("refs-proj")
    tau = _task("the cited task", project_id=project.id)
    trace_id = "3fd8137e-e0fd-4c1a-9b7e-0123456789ab"
    _session(trace_id, "Sigma session")
    lesson = memory.remember("a lesson body", title="Mu lesson")
    body = (f"[T{tau} x](task:{tau}) [S3fd8137e x](session:{trace_id}) "
            f"[M x](memory:{lesson}) [docs/a b.md](file:docs/a%20b.md) "
            "[F999999 Gone](feature:999999)")

    lines = _lines(run(_task(body, project_id=project.id)))

    def read(kind, *argv):
        return " — read: " + _cli_command(kind, *argv)

    assert lines == [
        f"- T{tau} 「shelf label」 — task, status idea"
        + read("task", "show", str(tau)),
        "- S3fd8137e 「Sigma session」 — session"
        + read("trace", "dump", trace_id, "--index"),
        f"- M{lesson[:8]} 「Mu lesson」 — memory"
        + read("memory", "read", lesson),
        "- file docs/a b.md in project refs-proj",
        "- feature:999999 — not found",
    ]
    for line in lines[:3]:
        argv = shlex.split(line.split(" — read: ", 1)[1])[2:]
        result = CliRunner().invoke(app, argv)
        assert result.exit_code == 0, (argv, result.output)


def test_references_are_listed_once_and_past_twenty_are_counted(run):
    cited = [feature_store.create(f"Cited {n}")
             for n in range(REFERENCES_MAX + 2)]
    host = feature_store.create(
        "Host", body=f"Again: [F{cited[0].id} x](feature:{cited[0].id})")
    body = " ".join(f"[F{row.id} x](feature:{row.id})" for row in cited)
    body += f" and once more [F{cited[1].id} x](feature:{cited[1].id})"

    lines = _lines(run(_task(body, feature_id=host.id)))

    assert [line.split(" ", 2)[1] for line in lines[:-1]] == [
        f"F{row.id}" for row in cited[:REFERENCES_MAX]]
    assert lines[-1] == "… and 2 more references"


def test_a_file_line_stays_one_line():
    """A git path or a repo name can hold a line break; the block is one line
    per reference regardless."""
    from lib.refs.resolve import Project, file_item

    item = file_item("a%0Ab.md", "a\nb.md", Project(id=1, name="re gin",
                                                   path="/nonexistent"))

    assert (_reference_line("file:a%0Ab.md", item)
            == "- file a b.md in project re gin")


def test_a_dropped_project_does_not_stop_the_launch(run):
    """A task's project id carries no foreign key, so its repo can be gone by
    the time it runs; the file line just loses its project."""
    project = _project("gone")
    task_id = _task("See [docs/a.md](file:docs/a.md).", project_id=project.id)
    with SessionLocal() as session:
        session.delete(session.get(Repo, project.id))
        session.commit()

    assert _lines(run(task_id)) == ["- file docs/a.md"]


def test_a_fix_task_reads_its_defect_and_feature_as_they_are_now(run):
    """The fix task cites rather than copies, so a defect reworded, re-pathed
    and closed — and a feature renamed — after the task was cut is what its
    run reads, and the task row itself is never rewritten."""
    from lib.defects import fix_items  # noqa: PLC0415
    from lib.orm.models.tasks import AgentTask  # noqa: PLC0415

    feature = feature_store.create("Old name")
    defect = defect_store.record("old wording", feature_id=feature.id,
                                 session_id="t-11",
                                 suspect_paths=["lib/a.py"])
    task_id = fix_items.create_for(defect.id).fix_task_id
    with SessionLocal() as session:
        cut = session.get(AgentTask, task_id).body

    defect_store.edit(defect.id, symptom="new wording",
                      suspect_paths=["lib/b.py", "lib/c.py"])
    defect_store.close(defect.id, missing_check="test_new")
    feature_store.rename(feature.id, "New name")
    stage = _stage(feature.id)
    prompt = run(task_id)

    with SessionLocal() as session:
        assert session.get(AgentTask, task_id).body == cut
    assert _lines(prompt) == [
        f"- D{defect.id} 「new wording」 — defect, status closed, "
        f"feature #{feature.id} — reported in session t-11 — "
        "suspect paths: lib/b.py, lib/c.py — missing check: test_new",
        f"- F{feature.id} 「New name」 — feature, stage {stage} — read: "
        + _cli_command("feature", "spec", str(feature.id)),
    ]
