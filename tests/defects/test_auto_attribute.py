"""An unattributed defect the operator cannot place is attributed by the agent,
which unblocks its fix task. The agent may only name a live feature it was
shown; any other answer writes nothing."""

from __future__ import annotations

from sqlmodel import select
from typer.testing import CliRunner

from lib.auth import create_token
from lib.defects import auto_attribute, store
from lib.features import store as feature_store
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}


class _Agent:
    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    def complete(self, prompt, **_):
        self.prompts.append(prompt)
        return self.answer(prompt) if callable(self.answer) else self.answer


def _agent(monkeypatch, answer):
    agent = _Agent(answer)
    monkeypatch.setattr(auto_attribute, "_llm", lambda: agent)
    return agent


def _post(client, defect_id, **body):
    return client.post(f"/api/defects/{defect_id}/auto-attribute",
                       json=body, headers=EDITOR)


def test_the_agents_pick_attributes_and_files_the_fix_task(
        flask_client, feature_row, monkeypatch):
    feature_row("unrelated")
    target = feature_row("trace merge")
    row = store.record("the gutter folds", session_id="t-1",
                       suspect_paths=["lib/trace/merge.py"])
    agent = _agent(monkeypatch,
                   f'thinking…\n{{"feature_id": {target.id}, '
                   '"reason": "merge.py is that feature\'s"}')

    answer = _post(flask_client, row.id, fix_task=True)

    assert answer.status_code == 200, answer.get_json()
    body = answer.get_json()
    assert body["reason"] == "merge.py is that feature's"
    assert store.get(row.id).feature_id == target.id
    with SessionLocal() as session:
        task = session.get(AgentTask, store.get(row.id).fix_task_id)
    assert task.feature_id == target.id
    prompt = agent.prompts[0]
    assert f"F{target.id} | trace merge" in prompt
    assert "lib/trace/merge.py" in prompt and "the gutter folds" in prompt


def test_without_fix_task_it_only_attributes(flask_client, feature_row,
                                             monkeypatch):
    target = feature_row()
    row = store.record("it broke", session_id="t-1")
    _agent(monkeypatch, f'{{"feature_id": "F{target.id}", "reason": "r"}}')

    assert _post(flask_client, row.id).status_code == 200
    assert store.get(row.id).feature_id == target.id
    assert store.get(row.id).fix_task_id is None


def _nothing_written(row_id):
    with SessionLocal() as session:
        tasks = session.exec(select(AgentTask)).all()
    return store.get(row_id).feature_id is None and not tasks


def test_an_id_outside_the_candidates_is_refused(flask_client, feature_row,
                                                monkeypatch):
    feature_row()
    row = store.record("it broke", session_id="t-1")
    _agent(monkeypatch, '{"feature_id": 99999, "reason": "made up"}')

    answer = _post(flask_client, row.id, fix_task=True)

    assert answer.status_code == 409
    assert "99999" in answer.get_json()["error"]
    assert _nothing_written(row.id)


def test_an_abandoned_feature_is_not_a_candidate(flask_client, feature_row,
                                                monkeypatch):
    feature_row("live")
    gone = feature_row("dropped")
    feature_store.abandon(gone.id)
    row = store.record("it broke", session_id="t-1")
    agent = _agent(monkeypatch, f'{{"feature_id": {gone.id}}}')

    assert _post(flask_client, row.id).status_code == 409
    assert agent.prompts and f"F{gone.id} |" not in agent.prompts[0]
    assert store.get(row.id).feature_id is None


def test_null_prose_or_silence_writes_nothing(flask_client, feature_row,
                                             monkeypatch):
    feature_row()
    row = store.record("it broke", session_id="t-1")
    for answer in ('{"feature_id": null, "reason": "no match"}',
                   "I think it is the trace one", None):
        _agent(monkeypatch, answer)
        refused = _post(flask_client, row.id, fix_task=True)
        assert refused.status_code == 409, answer
        assert "by hand" in refused.get_json()["error"]
        assert _nothing_written(row.id)


def test_no_configured_agent_is_a_refusal(flask_client, feature_row,
                                          monkeypatch):
    feature_row()
    row = store.record("it broke", session_id="t-1")
    monkeypatch.setattr(auto_attribute, "_llm", lambda: None)

    answer = _post(flask_client, row.id)

    assert answer.status_code == 409
    assert "external agent" in answer.get_json()["error"]


def test_an_attributed_defect_is_refused_before_the_agent_runs(
        flask_client, feature_row, monkeypatch):
    owner = feature_row()
    row = store.record("it broke", feature_id=owner.id, session_id="t-1")
    agent = _agent(monkeypatch, "{}")

    assert _post(flask_client, row.id).status_code == 409
    assert agent.prompts == []


def test_bad_flag_and_missing_defect(flask_client):
    assert _post(flask_client, 1, fix_task="yes").status_code == 400
    assert _post(flask_client, 424242).status_code == 404


def test_parse_answer_takes_the_last_object_and_rejects_bools():
    assert auto_attribute.parse_answer(
        '{"feature_id": 1} then {"feature_id": 7, "reason": "x"}') == (7, "x")
    assert auto_attribute.parse_answer('{"feature_id": true}') == (None, "")
    assert auto_attribute.parse_answer("no json") == (None, "")


def test_cli_auto_flag(feature_row, monkeypatch):
    from cli.commands.defects import defects_app

    target = feature_row()
    row = store.record("it broke", session_id="t-1")
    _agent(monkeypatch, f'{{"feature_id": {target.id}, "reason": "because"}}')
    runner = CliRunner()

    both = runner.invoke(defects_app, ["attribute", str(row.id), "5", "--auto"])
    assert both.exit_code == 2
    done = runner.invoke(defects_app, ["attribute", str(row.id), "--auto"])
    assert done.exit_code == 0, done.output
    assert "because" in done.output
    assert store.get(row.id).feature_id == target.id


def test_a_refused_fix_task_keeps_the_attribution_and_says_so(
        flask_client, feature_row, monkeypatch):
    target = feature_row()
    row = store.record("it broke", session_id="t-1")
    _agent(monkeypatch, f'{{"feature_id": {target.id}, "reason": "r"}}')

    def refuse(_defect_id):
        raise store.DefectError("already points at task #9")
    monkeypatch.setattr(auto_attribute.fix_items, "create_for", refuse)

    answer = _post(flask_client, row.id, fix_task=True)

    assert answer.status_code == 200
    assert "task #9" in answer.get_json()["fix_error"]
    assert store.get(row.id).feature_id == target.id


def test_a_hand_attribution_during_the_agent_call_wins(
        flask_client, feature_row, monkeypatch):
    human, agent_pick = feature_row("human"), feature_row("agent")
    row = store.record("it broke", session_id="t-1")

    def answer(_prompt):
        store.attribute(row.id, human.id)
        return f'{{"feature_id": {agent_pick.id}}}'
    _agent(monkeypatch, answer)

    refused = _post(flask_client, row.id)

    assert refused.status_code == 409
    assert "meanwhile" in refused.get_json()["error"]
    assert store.get(row.id).feature_id == human.id


def test_parse_answer_survives_braces_in_the_reason():
    assert auto_attribute.parse_answer(
        '{"feature_id": 5, "reason": "uses {x} in merge.py"}') \
        == (5, "uses {x} in merge.py")
    assert auto_attribute.parse_answer(
        '{"feature_id": 6, "meta": {"a": 1}}')[0] == 6
