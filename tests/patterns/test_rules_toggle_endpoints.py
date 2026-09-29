"""Characterization of the pattern rule toggles: `/rules/{enable,disable}`
(`_toggle_pattern_rules`) and `/bundle-rules/<action>`
(`api_toggle_bundle_rules`), in web/blueprints/patterns/rules_toggle.py."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lib.auth import create_token


def _auth():
    return {"Authorization": f"Bearer {create_token(1, 'editor-tester', 'editor')}"}


@pytest.fixture
def grit(monkeypatch):
    from web.blueprints.patterns import rules_toggle as rt
    state = {"rules": [{"id": "r1"}, {"id": "r2"}], "set": [], "pushed": [],
             "skill_id": None, "deployed": True, "push_raises": False,
             "regen": 0, "index_deploys": 0}
    monkeypatch.setattr(rt.grit_rule_index, "rules_for_guide",
                        lambda slug: state["rules"])
    monkeypatch.setattr(rt.grit_rule_index, "set_rules_disabled",
                        lambda ids, disabled: state["set"].append((ids, disabled)))

    def regen(write_guides):
        state["regen"] += 1
    monkeypatch.setattr(rt.grit_rule_index, "regenerate", regen)

    def deploy_index(path):
        state["index_deploys"] += 1
    monkeypatch.setattr(rt._pkg, "deploy_rules_index_skill", deploy_index)
    monkeypatch.setattr(rt.skill_registry, "skill_id_for_procedure",
                        lambda slug: state["skill_id"])
    monkeypatch.setattr(rt.skill_registry, "deployed_exists",
                        lambda sid: state["deployed"])

    def push(sid, force):
        if state["push_raises"]:
            raise RuntimeError("disk full")
        state["pushed"].append(sid)
        return "pushed ok"
    monkeypatch.setattr(rt.skill_sync, "push", push)
    return state


def _post(client, url, body=None):
    resp = client.post(url, json=body or {}, headers=_auth())
    return resp.status_code, resp.get_json()


@pytest.mark.parametrize("verb,body,rules,skill,expected,set_calls", [
    ("disable", None, [], (None, True, False),
     (200, {"ok": False, "msg": "no rules linked"}), []),
    ("disable", {"rule_ids": ["zz"]}, None, (None, True, False),
     (200, {"ok": False, "msg": "no requested rules belong to this pattern"}), []),
    ("disable", {"rule_ids": ["r2", "zz"]}, None, (None, True, False),
     (200, {"ok": True, "msg": "disabled 1 rule(s): r2"}), [(["r2"], True)]),
    ("disable", None, None, ("sk", False, False),
     (200, {"ok": True, "msg": "disabled 2 rule(s): r1, r2"}), [(["r1", "r2"], True)]),
    ("enable", None, None, (None, False, False),
     (200, {"ok": True, "msg": "enabled 2 rule(s): r1, r2"}), [(["r1", "r2"], False)]),
    ("enable", None, None, ("sk", True, False),
     (200, {"ok": True, "msg": "enabled 2 rule(s): r1, r2"}), [(["r1", "r2"], False)]),
    ("enable", None, None, ("sk", False, False),
     (200, {"ok": True,
            "msg": "enabled 2 rule(s): r1, r2 (deployed skill: pushed ok)"}),
     [(["r1", "r2"], False)]),
    ("enable", None, None, ("sk", False, True),
     (500, {"ok": False,
            "msg": "enabled 2 rule(s) but skill deploy failed: disk full"}),
     [(["r1", "r2"], False)]),
])
def test_toggle_pattern_rules(flask_client, tmp_db, grit, verb, body, rules,
                              skill, expected, set_calls):
    if rules is not None:
        grit["rules"] = rules
    grit["skill_id"], grit["deployed"], grit["push_raises"] = skill
    got = _post(flask_client, f"/api/patterns/p/rules/{verb}", body)
    assert got == expected
    assert grit["set"] == set_calls
    finished = expected[0] == 200 and expected[1]["ok"]
    assert (grit["regen"], grit["index_deploys"]) == ((1, 1) if finished else (0, 0))


@pytest.fixture
def bundles(monkeypatch):
    from web.blueprints.patterns import rules_toggle as rt
    calls = []

    def engine(eid, ids):
        return SimpleNamespace(
            id=eid, parse_rules=lambda: [SimpleNamespace(id=i) for i in ids])
    monkeypatch.setattr(rt.rule_engines, "all_engines", lambda: [
        engine("style", ["a", "b"]), engine("other", ["c"])])
    monkeypatch.setattr(rt.engine_rule_disable, "set_disabled",
                        lambda eid, ids, disabled: calls.append((eid, ids, disabled)))
    return calls


@pytest.mark.parametrize("action,body,expected,calls", [
    ("toggle", {"engine_id": "style", "rule_ids": ["a"]},
     (400, {"ok": False, "msg": "action must be disable or enable"}), []),
    ("disable", {"rule_ids": ["a"]},
     (400, {"ok": False, "msg": "engine_id required"}), []),
    ("disable", {"engine_id": "style", "rule_ids": "a"},
     (400, {"ok": False, "msg": "rule_ids must be a non-empty list"}), []),
    ("enable", {"engine_id": "style"},
     (400, {"ok": False, "msg": "rule_ids must be a non-empty list"}), []),
    ("disable", {"engine_id": "style", "rule_ids": ["c"]},
     (400, {"ok": False, "msg": "no rule_ids belong to engine 'style'"}), []),
    ("disable", {"engine_id": "style", "rule_ids": ["b", "c", "a"]},
     (200, {"ok": True, "msg": "disabled 2 style rule(s): b, a"}),
     [("style", ["b", "a"], True)]),
    ("enable", {"engine_id": "other", "rule_ids": ["c"]},
     (200, {"ok": True, "msg": "enabled 1 other rule(s): c"}),
     [("other", ["c"], False)]),
])
def test_toggle_bundle_rules(flask_client, tmp_db, bundles, action, body,
                             expected, calls):
    got = _post(flask_client, f"/api/patterns/p/bundle-rules/{action}", body)
    assert (got, bundles) == (expected, calls)
