"""Unit tests for the Option-B per-rule triggers endpoints.

Covers:
- GET /api/triggers/rules (list with KPIs, sparks, status, filters, sort)
- GET /api/triggers/rules/<rule_id> (drawer detail with guide fallback)
- GET /api/settings/rule-triggers/thresholds (read-only)

These ship alongside the existing /api/triggers (raw events log)
endpoint in test_blueprint_rules.py — they are not a replacement.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lib.orm import SessionLocal
from lib.orm.models import RuleTrigger
from lib.rule_engines.base import Rule


def _iso(dt: datetime) -> str:
    """ISO-8601 string sans microseconds — matches what SQLite stores."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _seed(session, rule_id: str, fires: int, misses: int, *, when=None,
          severity="warn", session_id=None, file_path=None) -> None:
    """Add `fires` fired rows + `misses` non-fired rows for one rule."""
    when = when or datetime.now(timezone.utc)
    for i in range(fires):
        session.add(RuleTrigger(
            rule_id=rule_id, file_path=file_path or f"{rule_id}_{i}.java",
            match_count=1, triggered=1, severity=severity,
            session_id=session_id, checked_at=_iso(when),
        ))
    for i in range(misses):
        session.add(RuleTrigger(
            rule_id=rule_id, file_path=file_path or f"{rule_id}_m{i}.java",
            match_count=0, triggered=0, severity=severity,
            session_id=session_id, checked_at=_iso(when),
        ))


@pytest.fixture
def fake_engines(monkeypatch):
    """Stub the engine registry so `_all_configured_rules()` returns a
    known set of rules without touching real grit files on disk.

    The list endpoint resolves severity/source/guide_preview from these,
    so any rule referenced in a test must be declared here for full
    fidelity.
    """
    rules = [
        Rule(id="noisy_rule", engine="grit", summary="be tidy",
             severity="warn", triggers=("*.java",), source_file="x.grit",
             metadata={"guide": "Do not write spaghetti"}),
        Rule(id="active_rule", engine="grit", summary="be polite",
             severity="error", triggers=("*.java",), source_file="y.grit",
             metadata={"guide": "Use guard clauses"}),
        Rule(id="dead_rule", engine="grit", summary="dead one",
             severity="info", triggers=("*.java",), source_file="z.grit",
             metadata={"guide": "Never matches"}),
    ]

    class _StubEngine:
        id = "grit"
        kind = "grit"
        def parse_rules(self):
            return rules

    from web.blueprints import rules as rules_bp
    monkeypatch.setattr(rules_bp.rule_engines, "all_engines",
                        lambda: [_StubEngine()])
    return rules


# ── GET /api/triggers/rules ─────────────────────────────────

def test_triggers_rules_classifies_noisy_active_dead(flask_client, fake_engines, tmp_db):
    """Status classification is the heart of the page — verify each
    branch fires correctly with the default thresholds (noisy ≥30% AND
    ≥5 fires; dead = 0 fires AND ≥3 checks; active otherwise)."""
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    with SessionLocal() as session:
        # 7 fires / 10 checks = 70% — well above both noisy gates.
        _seed(session, "noisy_rule", fires=7, misses=3, when=when)
        # 2 fires / 10 checks = 20% — fires but not noisy.
        _seed(session, "active_rule", fires=2, misses=8, when=when)
        # 0 fires / 5 checks — dead.
        _seed(session, "dead_rule", fires=0, misses=5, when=when)
        session.commit()

    body = flask_client.get("/api/triggers/rules?range=7d").get_json()
    statuses = {r["rule_id"]: r["status"] for r in body["rules"]}
    assert statuses["noisy_rule"] == "noisy"
    assert statuses["active_rule"] == "active"
    assert statuses["dead_rule"] == "dead"

    kpis = body["kpis"]
    assert kpis["configured"] == 3          # three engine-known rules
    assert kpis["noisy"] == 1
    assert kpis["dead"] == 1
    assert kpis["active"] == 1


def test_triggers_rules_low_n_is_not_noisy(flask_client, fake_engines, tmp_db):
    """A rule with 1 fire / 2 checks (50% by rate) must NOT be noisy —
    the noisy_min_fires gate exists to suppress low-N false positives."""
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    with SessionLocal() as session:
        _seed(session, "noisy_rule", fires=1, misses=1, when=when)
        session.commit()

    body = flask_client.get("/api/triggers/rules?range=7d").get_json()
    row = next(r for r in body["rules"] if r["rule_id"] == "noisy_rule")
    assert row["status"] == "active"
    assert row["trigger_rate_pct"] == 50
    assert body["kpis"]["noisy"] == 0


def test_triggers_rules_range_excludes_old_rows(flask_client, fake_engines, tmp_db):
    """A row dated 40 days back must not appear in a 7d window."""
    long_ago = datetime.now(timezone.utc) - timedelta(days=40)
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    with SessionLocal() as session:
        _seed(session, "noisy_rule", fires=5, misses=0, when=long_ago)
        _seed(session, "active_rule", fires=1, misses=4, when=recent)
        session.commit()

    body = flask_client.get("/api/triggers/rules?range=7d").get_json()
    noisy = next(r for r in body["rules"] if r["rule_id"] == "noisy_rule")
    active = next(r for r in body["rules"] if r["rule_id"] == "active_rule")
    # Old rows excluded: noisy_rule has 0 fires + 0 checks in window.
    assert noisy["fires"] == 0
    assert noisy["checks"] == 0
    # Recent rows survive.
    assert active["fires"] == 1
    assert active["checks"] == 5


def test_triggers_rules_spark_bucket_count_matches_range(flask_client, fake_engines, tmp_db):
    """24h→24 buckets, 7d→7, 30d→30, all→12 — zero-filled."""
    expected = {"24h": 24, "7d": 7, "30d": 30, "all": 12}
    for range_str, count in expected.items():
        body = flask_client.get(f"/api/triggers/rules?range={range_str}").get_json()
        # Every rule's spark must have exactly this many buckets.
        for rule in body["rules"]:
            assert len(rule["spark"]) == count, (
                f"range={range_str} rule={rule['rule_id']} "
                f"got {len(rule['spark'])} buckets, expected {count}"
            )


def test_triggers_rules_sort_by_rate(flask_client, fake_engines, tmp_db):
    """Default sort = trigger rate descending; ties broken by fires."""
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    with SessionLocal() as session:
        _seed(session, "noisy_rule", fires=7, misses=3, when=when)   # 70%
        _seed(session, "active_rule", fires=2, misses=8, when=when)  # 20%
        session.commit()

    body = flask_client.get("/api/triggers/rules?sort=rate&range=7d").get_json()
    # First two should be noisy then active by rate (dead_rule has 0 rate,
    # comes last).
    order = [r["rule_id"] for r in body["rules"]]
    assert order.index("noisy_rule") < order.index("active_rule")


def test_triggers_rules_filter_by_marks(flask_client, fake_engines, tmp_db):
    """`marks=1` narrows the list to rules with at least one
    user-suppressed event in the active range — distinct from the
    rate-based `status=noisy` classification."""
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    with SessionLocal() as session:
        _seed(session, "active_rule", fires=2, misses=0, when=when)
        _seed(session, "noisy_rule",  fires=7, misses=3, when=when)
        session.commit()

    # Suppress one event of active_rule via the API.
    from sqlmodel import select as _sel
    with SessionLocal() as session:
        target = session.exec(
            _sel(RuleTrigger).where(RuleTrigger.rule_id == "active_rule").limit(1)
        ).first()
    flask_client.post(f"/api/triggers/{target.id}/suppress",
                       headers=_editor_auth(), json={"reason": "fp"})

    # Plain query returns both rules.
    plain = flask_client.get("/api/triggers/rules?range=7d").get_json()
    plain_ids = {r["rule_id"] for r in plain["rules"]}
    assert {"active_rule", "noisy_rule"}.issubset(plain_ids)

    # marks=1 narrows to just the one with a suppression.
    marked = flask_client.get("/api/triggers/rules?range=7d&marks=1").get_json()
    marked_ids = [r["rule_id"] for r in marked["rules"]]
    assert marked_ids == ["active_rule"]
    assert marked["rules"][0]["suppressed_count"] == 1


def test_triggers_rules_filter_by_status(flask_client, fake_engines, tmp_db):
    """status=noisy returns only noisy rules."""
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    with SessionLocal() as session:
        _seed(session, "noisy_rule", fires=7, misses=3, when=when)
        _seed(session, "active_rule", fires=2, misses=8, when=when)
        _seed(session, "dead_rule", fires=0, misses=5, when=when)
        session.commit()

    body = flask_client.get("/api/triggers/rules?status=noisy&range=7d").get_json()
    assert [r["rule_id"] for r in body["rules"]] == ["noisy_rule"]
    # KPIs are pre-filter — still reflect the whole population.
    assert body["kpis"]["dead"] == 1


def test_triggers_rules_top_files_basenames(flask_client, fake_engines, tmp_db):
    """top_files reports basename + n, ordered by n desc, capped at 3."""
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    with SessionLocal() as session:
        for path, n in [("/repos/svc/Order.java", 5),
                         ("/repos/svc/Cart.java", 3),
                         ("/repos/svc/Payment.java", 8),
                         ("/repos/svc/Refund.java", 1)]:
            _seed(session, "noisy_rule", fires=n, misses=0,
                  when=when, file_path=path)
        session.commit()

    body = flask_client.get("/api/triggers/rules?range=7d").get_json()
    row = next(r for r in body["rules"] if r["rule_id"] == "noisy_rule")
    names = [f["name"] for f in row["top_files"]]
    assert names == ["Payment.java", "Order.java", "Cart.java"]
    assert len(row["top_files"]) == 3


def test_triggers_rules_rejects_invalid_range(flask_client, fake_engines, tmp_db):
    resp = flask_client.get("/api/triggers/rules?range=999d")
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


# ── GET /api/triggers/rules/<rule_id> ───────────────────────

def test_triggers_rule_detail_guide_fallback_from_engine(
        flask_client, fake_engines, tmp_db):
    """When all DB rows have NULL guide (the 98% case in production),
    the drawer falls back to the engine's metadata guide."""
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    with SessionLocal() as session:
        session.add(RuleTrigger(
            rule_id="active_rule", file_path="X.java",
            match_count=1, triggered=1, severity="error",
            guide=None,                                   # <-- intentional
            checked_at=_iso(when),
        ))
        session.commit()

    body = flask_client.get("/api/triggers/rules/active_rule?range=7d").get_json()
    assert body["guide"] == "Use guard clauses"      # from fake_engines fixture


def test_triggers_rule_detail_prefers_db_guide_when_set(
        flask_client, fake_engines, tmp_db):
    """When the DB row carries a guide, it wins over engine metadata —
    preserving "what the agent actually saw at the time" semantics."""
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    with SessionLocal() as session:
        session.add(RuleTrigger(
            rule_id="active_rule", file_path="X.java",
            match_count=1, triggered=1, severity="error",
            guide="historical guidance text",
            checked_at=_iso(when),
        ))
        session.commit()

    body = flask_client.get("/api/triggers/rules/active_rule?range=7d").get_json()
    assert body["guide"] == "historical guidance text"


def test_triggers_rule_detail_events_include_span_id(
        flask_client, fake_engines, tmp_db):
    """span_id key must always be present (null until PR-2)."""
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    with SessionLocal() as session:
        session.add(RuleTrigger(
            rule_id="active_rule", file_path="X.java",
            match_count=1, triggered=1, checked_at=_iso(when),
        ))
        session.commit()

    body = flask_client.get("/api/triggers/rules/active_rule?range=7d").get_json()
    assert len(body["events"]) == 1
    assert "span_id" in body["events"][0]
    assert body["events"][0]["span_id"] is None


def test_triggers_rule_detail_events_filtered_to_matched_only(
        flask_client, fake_engines, tmp_db):
    """Drawer's events list shows matched rows only. The full check log
    (including triggered=0 misses) lives at /trace/triggers/raw."""
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    with SessionLocal() as session:
        session.add(RuleTrigger(
            rule_id="active_rule", file_path="hit.java",
            match_count=1, triggered=1, checked_at=_iso(when),
        ))
        session.add(RuleTrigger(
            rule_id="active_rule", file_path="miss.java",
            match_count=0, triggered=0, checked_at=_iso(when),
        ))
        session.commit()

    body = flask_client.get("/api/triggers/rules/active_rule?range=7d").get_json()
    assert len(body["events"]) == 1
    assert body["events"][0]["file_path"] == "hit.java"
    assert body["events"][0]["triggered"] == 1


def test_triggers_rule_detail_sessions_have_last_seen_not_plan(
        flask_client, fake_engines, tmp_db):
    """Sessions block carries last_seen (replaces the plan_filename
    column the UI used to render — plan names are auto-generated by
    Claude and carry no signal for triage)."""
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    with SessionLocal() as session:
        session.add(RuleTrigger(
            rule_id="active_rule", file_path="X.java",
            match_count=1, triggered=1,
            session_id="session-abc",
            checked_at=_iso(when),
        ))
        session.commit()

    body = flask_client.get("/api/triggers/rules/active_rule?range=7d").get_json()
    assert len(body["sessions"]) == 1
    sess = body["sessions"][0]
    assert sess["session_id"] == "session-abc"
    assert "last_seen" in sess
    assert sess["last_seen"] is not None
    assert "plan_filename" not in sess


# ── GET /api/settings/rule-triggers/thresholds ──────────────

def test_thresholds_endpoint_returns_defaults(flask_client, tmp_db):
    body = flask_client.get("/api/settings/rule-triggers/thresholds").get_json()
    assert body == {
        "noisy_min_rate_pct": 30,
        "noisy_min_fires": 5,
        "dead_min_checks": 3,
        "default_range": "7d",
    }


# ── PUT /api/settings/rule-triggers/thresholds ──────────────

def _admin_auth():
    from lib.auth import create_token
    token = create_token(1, "admin-tester", "admin")
    return {"Authorization": f"Bearer {token}"}


def _editor_auth():
    from lib.auth import create_token
    token = create_token(2, "editor-tester", "editor")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """Redirect save_settings writes to a throwaway file so the test
    doesn't mutate the real config/settings.json. Also stubs
    reload_settings to a no-op for the same reason."""
    import lib.settings as _cfg
    fake_path = tmp_path / "settings.json"
    fake_path.write_text("{}")
    monkeypatch.setattr(_cfg, "SETTINGS_PATH", str(fake_path))
    monkeypatch.setattr(_cfg, "SETTINGS_LOCAL_PATH",
                        str(tmp_path / "settings.local.json"))
    # save_settings calls reload_settings() at the tail — that re-reads
    # the real config dir, which would clobber our test. Stub it.
    import lib.settings as _ls
    monkeypatch.setattr(_ls, "reload_settings", lambda: _ls.settings)
    return fake_path


def test_thresholds_write_requires_auth(anon_client, tmp_db):
    resp = anon_client.put("/api/settings/rule-triggers/thresholds",
                            json={"noisy_min_rate_pct": 50})
    assert resp.status_code == 401


def test_thresholds_write_rejects_editor(flask_client, tmp_db, isolated_settings):
    resp = flask_client.put("/api/settings/rule-triggers/thresholds",
                            json={"noisy_min_rate_pct": 50},
                            headers=_editor_auth())
    assert resp.status_code == 403


def test_thresholds_write_persists_for_admin(flask_client, tmp_db, isolated_settings):
    resp = flask_client.put("/api/settings/rule-triggers/thresholds",
                            json={
                                "noisy_min_rate_pct": 40,
                                "noisy_min_fires": 8,
                                "dead_min_checks": 4,
                                "default_range": "30d",
                            },
                            headers=_admin_auth())
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["thresholds"]["noisy_min_rate_pct"] == 40
    # File on disk shows the new block — proves the persistence path.
    import json
    saved = json.loads(isolated_settings.read_text())
    assert saved["rule_trigger_thresholds"]["noisy_min_rate_pct"] == 40
    assert saved["rule_trigger_thresholds"]["default_range"] == "30d"


def test_thresholds_write_validates_input(flask_client, tmp_db, isolated_settings):
    resp = flask_client.put("/api/settings/rule-triggers/thresholds",
                            json={
                                "noisy_min_rate_pct": 200,    # > 100
                                "default_range": "bogus",     # not in VALID_RANGES
                            },
                            headers=_admin_auth())
    assert resp.status_code == 400
    errors = resp.get_json()["errors"]
    assert any("noisy_min_rate_pct" in e for e in errors)
    assert any("default_range" in e for e in errors)


# ── POST /api/triggers/reset (admin gate) ──────────────────

def test_reset_requires_admin(flask_client, tmp_db):
    """Editor was allowed pre-PR-3; now reset is admin-only because
    the trigger log is treated as observability data."""
    resp = flask_client.post("/api/triggers/reset", json={},
                              headers=_editor_auth())
    assert resp.status_code == 403


def test_reset_allowed_for_admin(flask_client, tmp_db, fake_engines):
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    with SessionLocal() as session:
        _seed(session, "active_rule", fires=2, misses=0, when=when)
        session.commit()
    resp = flask_client.post("/api/triggers/reset", json={},
                              headers=_admin_auth())
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["ok"] is True


# ── GET /api/triggers/stats ────────────────────────────────

def test_stats_returns_counts(flask_client, tmp_db, fake_engines):
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    with SessionLocal() as session:
        _seed(session, "active_rule", fires=2, misses=3, when=when)
        _seed(session, "noisy_rule", fires=4, misses=1, when=when)
        session.commit()
    body = flask_client.get("/api/triggers/stats").get_json()
    assert body["total"] == 10                # 5 + 5
    assert body["distinct_rules"] == 2
    assert body["oldest_at"] is not None
    # Per-age windows always present (backs the retention dropdown's
    # "→ N rows" preview). All zero here because every seeded row is
    # only 1h old.
    assert body["older_than"] == {"7": 0, "30": 0, "90": 0, "365": 0}


def test_stats_older_than_counts_split_by_age(
        flask_client, tmp_db, fake_engines):
    """Three rows: 1d, 60d, 400d old. Per-window counts should be
    cumulative-old (>= cutoff): 7d→2, 30d→2, 90d→1, 365d→1."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        for delta in (timedelta(days=1), timedelta(days=60), timedelta(days=400)):
            session.add(RuleTrigger(
                rule_id="active_rule", file_path=f"f-{delta.days}.java",
                match_count=1, triggered=1,
                checked_at=_iso(now - delta),
            ))
        session.commit()
    body = flask_client.get("/api/triggers/stats").get_json()
    assert body["total"] == 3
    # 7-day cutoff: 60d + 400d are older → 2.
    assert body["older_than"]["7"] == 2
    # 30-day cutoff: same.
    assert body["older_than"]["30"] == 2
    # 90-day cutoff: only the 400d row is older.
    assert body["older_than"]["90"] == 1
    # 365-day cutoff: same.
    assert body["older_than"]["365"] == 1


def test_reset_older_than_days_only_deletes_old_rows(
        flask_client, tmp_db, fake_engines):
    """Retention policy: rows newer than the cutoff stay."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        session.add(RuleTrigger(
            rule_id="r", file_path="new.java", match_count=1, triggered=1,
            checked_at=_iso(now - timedelta(days=3)),
        ))
        session.add(RuleTrigger(
            rule_id="r", file_path="mid.java", match_count=1, triggered=1,
            checked_at=_iso(now - timedelta(days=10)),
        ))
        session.add(RuleTrigger(
            rule_id="r", file_path="old.java", match_count=1, triggered=1,
            checked_at=_iso(now - timedelta(days=400)),
        ))
        session.commit()

    resp = flask_client.post(
        "/api/triggers/reset",
        json={"older_than_days": 7},
        headers=_admin_auth(),
    )
    assert resp.status_code == 200

    with SessionLocal() as session:
        from sqlmodel import select as _sel
        remaining = sorted(
            r.file_path for r in session.exec(_sel(RuleTrigger)).all()
        )
    assert remaining == ["new.java"]            # 3-day row survives


def test_reset_rejects_negative_older_than_days(
        flask_client, tmp_db, fake_engines):
    resp = flask_client.post(
        "/api/triggers/reset",
        json={"older_than_days": -1},
        headers=_admin_auth(),
    )
    assert resp.status_code == 400


def test_reset_rejects_non_integer_older_than_days(
        flask_client, tmp_db, fake_engines):
    resp = flask_client.post(
        "/api/triggers/reset",
        json={"older_than_days": "soon"},
        headers=_admin_auth(),
    )
    assert resp.status_code == 400


# ── POST/DELETE /api/triggers/<id>/suppress ─────────────────

def _pick_trigger_id():
    """Return the id of an arbitrary RuleTrigger row from the test DB."""
    from sqlmodel import select as _sel
    with SessionLocal() as session:
        row = session.exec(_sel(RuleTrigger).limit(1)).first()
        return row.id


def test_suppress_requires_editor(flask_client, tmp_db):
    """A viewer-role caller cannot mark events as noise."""
    with SessionLocal() as session:
        session.add(RuleTrigger(rule_id="r", file_path="a.java",
                                match_count=1, triggered=1))
        session.commit()
    rid = _pick_trigger_id()

    from lib.auth import create_token
    viewer = {"Authorization": f"Bearer {create_token(1, 'viewer-tester', 'viewer')}"}
    resp = flask_client.post(f"/api/triggers/{rid}/suppress",
                              headers=viewer, json={})
    assert resp.status_code == 403


def test_suppress_flips_boolean_and_persists_metadata(flask_client, tmp_db):
    """Editor POST creates the suppression row, flips rule_triggers.suppressed,
    and surfaces the metadata on the GET drawer endpoint."""
    from lib.orm.models import RuleTriggerSuppression
    from sqlmodel import select as _sel

    when = datetime.now(timezone.utc) - timedelta(hours=1)
    with SessionLocal() as session:
        session.add(RuleTrigger(rule_id="some_rule", file_path="X.java",
                                match_count=2, triggered=1,
                                checked_at=_iso(when)))
        session.commit()
    rid = _pick_trigger_id()

    resp = flask_client.post(f"/api/triggers/{rid}/suppress",
                              headers=_editor_auth(),
                              json={"reason": "auto-generated file"})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["suppression"]["rule_trigger_id"] == rid
    assert body["suppression"]["reason"] == "auto-generated file"

    # Both rows visible in DB.
    with SessionLocal() as session:
        rt = session.get(RuleTrigger, rid)
        sup = session.exec(
            _sel(RuleTriggerSuppression)
            .where(RuleTriggerSuppression.rule_trigger_id == rid)
        ).first()
        assert rt.suppressed == 1
        assert sup is not None


def test_suppress_idempotent_updates_reason_when_provided(flask_client, tmp_db):
    """Re-POSTing on an already-suppressed event returns the prior row
    AND updates the reason when the caller passes a new one. The
    second click in the UI shouldn't silently drop the user's input."""
    with SessionLocal() as session:
        session.add(RuleTrigger(rule_id="x", file_path="a.java",
                                match_count=1, triggered=1))
        session.commit()
    rid = _pick_trigger_id()

    first = flask_client.post(f"/api/triggers/{rid}/suppress",
                                headers=_editor_auth(),
                                json={"reason": "first"}).get_json()
    second = flask_client.post(f"/api/triggers/{rid}/suppress",
                                 headers=_editor_auth(),
                                 json={"reason": "second"}).get_json()
    assert first["suppression"]["reason"] == "first"
    assert second["idempotent"] is True
    assert second["reason_updated"] is True
    assert second["suppression"]["reason"] == "second"

    # Re-POST with the SAME reason is a no-op — reason_updated reflects that.
    third = flask_client.post(f"/api/triggers/{rid}/suppress",
                                headers=_editor_auth(),
                                json={"reason": "second"}).get_json()
    assert third["idempotent"] is True
    assert third["reason_updated"] is False


def test_unsuppress_clears_both(flask_client, tmp_db):
    """DELETE removes the suppression row AND clears the boolean."""
    from lib.orm.models import RuleTriggerSuppression
    from sqlmodel import select as _sel

    with SessionLocal() as session:
        session.add(RuleTrigger(rule_id="x", file_path="a.java",
                                match_count=1, triggered=1))
        session.commit()
    rid = _pick_trigger_id()
    flask_client.post(f"/api/triggers/{rid}/suppress",
                        headers=_editor_auth(), json={})

    resp = flask_client.delete(f"/api/triggers/{rid}/suppress",
                                 headers=_editor_auth())
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    with SessionLocal() as session:
        rt = session.get(RuleTrigger, rid)
        sup = session.exec(
            _sel(RuleTriggerSuppression)
            .where(RuleTriggerSuppression.rule_trigger_id == rid)
        ).first()
        assert rt.suppressed == 0
        assert sup is None


def test_suppress_404_on_unknown_id(flask_client, tmp_db):
    resp = flask_client.post("/api/triggers/9999999/suppress",
                              headers=_editor_auth(), json={})
    assert resp.status_code == 404


def test_suppressed_event_drops_from_aggregates(
        flask_client, tmp_db, fake_engines):
    """The whole point of the feature: a suppressed event must not count
    in the rule's fires or checks, must not appear in the spark, and
    must surface a suppressed_count on the per-rule output."""
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    with SessionLocal() as session:
        # 3 fires + 2 misses = 5 checks, 60% trigger rate.
        _seed(session, "noisy_rule", fires=3, misses=2, when=when)
        session.commit()

    # Baseline: rule shows fires=3 / checks=5.
    before = flask_client.get("/api/triggers/rules?range=7d").get_json()
    row_before = next(r for r in before["rules"] if r["rule_id"] == "noisy_rule")
    assert (row_before["fires"], row_before["checks"]) == (3, 5)
    assert row_before["suppressed_count"] == 0

    # Suppress one of the fired events.
    from sqlmodel import select as _sel
    with SessionLocal() as session:
        target = session.exec(
            _sel(RuleTrigger)
            .where(RuleTrigger.rule_id == "noisy_rule")
            .where(RuleTrigger.triggered == 1)
            .limit(1)
        ).first()
    flask_client.post(f"/api/triggers/{target.id}/suppress",
                        headers=_editor_auth(),
                        json={"reason": "fixture noise"})

    # The fire AND the check both drop out — 2/4, not 2/5.
    after = flask_client.get("/api/triggers/rules?range=7d").get_json()
    row_after = next(r for r in after["rules"] if r["rule_id"] == "noisy_rule")
    assert (row_after["fires"], row_after["checks"]) == (2, 4)
    assert row_after["suppressed_count"] == 1


# ── GET /api/triggers/by-span/<span_id> ────────────────────

def test_by_span_returns_rows_with_suppression_state(
        flask_client, tmp_db, fake_engines):
    """The conversation view needs trigger ids per rule_id for the
    span it's showing — this endpoint backs that lookup."""
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    SPAN_ID = "deadbeefdeadbeef"
    with SessionLocal() as session:
        # Two different rules fired during the same PostToolUse span.
        session.add(RuleTrigger(rule_id="active_rule", file_path="A.java",
                                match_count=1, triggered=1,
                                span_id=SPAN_ID, checked_at=_iso(when)))
        session.add(RuleTrigger(rule_id="noisy_rule", file_path="A.java",
                                match_count=1, triggered=1,
                                span_id=SPAN_ID, checked_at=_iso(when)))
        # A different span — must NOT appear in the response.
        session.add(RuleTrigger(rule_id="active_rule", file_path="B.java",
                                match_count=1, triggered=1,
                                span_id="cafebabe", checked_at=_iso(when)))
        session.commit()

    body = flask_client.get(f"/api/triggers/by-span/{SPAN_ID}").get_json()
    by_rule = {t["rule_id"]: t for t in body["triggers"]}
    assert set(by_rule) == {"active_rule", "noisy_rule"}
    assert all(t["suppressed"] is False for t in by_rule.values())
    assert all(t["suppression"] is None for t in by_rule.values())


def test_by_span_surfaces_suppression_metadata(
        flask_client, tmp_db, fake_engines):
    """After suppressing one of the rule_trigger rows in a span, the
    by-span endpoint reports its suppressed=True + metadata so the
    UI can render the strikethrough + reason inline."""
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    SPAN_ID = "feedfeedfeedfeed"
    with SessionLocal() as session:
        session.add(RuleTrigger(rule_id="active_rule", file_path="A.java",
                                match_count=1, triggered=1,
                                span_id=SPAN_ID, checked_at=_iso(when)))
        session.commit()

    # Suppress it via the editor endpoint.
    tid = flask_client.get(f"/api/triggers/by-span/{SPAN_ID}")\
        .get_json()["triggers"][0]["id"]
    flask_client.post(f"/api/triggers/{tid}/suppress",
                       headers=_editor_auth(),
                       json={"reason": "span-side test"})

    body = flask_client.get(f"/api/triggers/by-span/{SPAN_ID}").get_json()
    t = body["triggers"][0]
    assert t["suppressed"] is True
    assert t["suppression"]["reason"] == "span-side test"


def test_by_span_empty_for_unknown_span(flask_client, tmp_db):
    body = flask_client.get("/api/triggers/by-span/no-such-span").get_json()
    assert body["triggers"] == []


def test_drawer_events_carry_suppression_metadata(
        flask_client, tmp_db, fake_engines):
    """Suppressed events are still listed in the drawer, with the
    metadata (who/when/reason) included so the UI can render the
    strikethrough + tooltip."""
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    with SessionLocal() as session:
        session.add(RuleTrigger(rule_id="active_rule", file_path="X.java",
                                match_count=1, triggered=1,
                                checked_at=_iso(when)))
        session.commit()
    rid = _pick_trigger_id()
    flask_client.post(f"/api/triggers/{rid}/suppress",
                        headers=_editor_auth(),
                        json={"reason": "drawer fixture"})

    body = flask_client.get(
        "/api/triggers/rules/active_rule?range=7d"
    ).get_json()
    assert len(body["events"]) == 1
    ev = body["events"][0]
    assert ev["suppressed"] is True
    assert ev["suppression"]["reason"] == "drawer fixture"
    assert ev["suppression"]["suppressed_by_username"] == "editor-tester"
