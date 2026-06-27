"""distill_session: LLM-only abstraction, self-scoring, and the
drop / queue / auto-approve routing it drives."""

from __future__ import annotations

import json

import lib.memory as memory
from lib.memory.distill import distill_session


class StubLLM:
    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    def complete(self, prompt, *, max_tokens=1024):
        self.prompts.append(prompt)
        return self.answer


def _llm(*items):
    """A StubLLM that returns the given proposal dicts as a JSON array."""
    return StubLLM(json.dumps(list(items)))


# A well-formed, mid-importance proposal whose tokens match the seeded
# session's pytest/ImportError signal — stays in the review queue.
_PYTEST_ITEM = {
    "title": "Use the venv interpreter for pytest",
    "body": ("Run pytest under .venv/bin/python; the system interpreter "
             "raises ImportError because it lacks the project deps."),
    "kind": "gotcha",
    "tags": ["venv", "pytest"],
}


def _insert_span(trace_id, name, attrs, start_time, span_id):
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO session_spans "
            "(trace_id, span_id, name, start_time, attributes, status_code) "
            "VALUES (?, ?, ?, ?, ?, 'OK')",
            (trace_id, span_id, name, start_time, json.dumps(attrs)))
        conn.commit()
    finally:
        conn.close()


def _seed_session(trace_id="sess-d"):
    _insert_span(trace_id, "tool.failure",
                 {"tool_name": "Bash", "command_preview": "pytest -x",
                  "error": "ImportError: no module named foo"},
                 "2026-06-11T10:00:00", "sp-fail")
    _insert_span(trace_id, "tool.Bash",
                 {"tool_name": "Bash", "command_preview": "pytest -x"},
                 "2026-06-11T10:01:00", "sp-fix")
    _insert_span(trace_id, "prompt",
                 {"text": "no, that's wrong — use the venv interpreter"},
                 "2026-06-11T10:02:00", "sp-prompt")


def _register_session_repo(trace_id, repo_name):
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        conn.execute("INSERT INTO repos (name, path) VALUES (?, ?)",
                     (repo_name, f"/tmp/{repo_name}"))
        repo_id = conn.execute("SELECT id FROM repos WHERE name = ?",
                               (repo_name,)).fetchone()["id"]
        conn.execute(
            "INSERT INTO session_repos (trace_id, repo_id, is_primary) "
            "VALUES (?, ?, 1)", (trace_id, repo_id))
        conn.commit()
    finally:
        conn.close()


def test_no_llm_proposes_nothing():
    """The signal is there (a failure→fix chain and a correction), but
    without an LLM to abstract it, distill writes nothing — heuristics no
    longer fabricate session-narrating proposals."""
    _seed_session()
    result = distill_session(memory.get_store(), "sess-d")
    assert result.source == "none"
    assert result.proposed == 0 and result.approved == 0
    assert result.memory_ids == []
    assert memory.get_store().list_memories(include_tests=True) == []


def test_distill_resolves_session_repo_scope():
    _seed_session()
    _register_session_repo("sess-d", "myrepo")
    distill_session(memory.get_store(), "sess-d", llm=_llm(_PYTEST_ITEM))
    rows = memory.get_store().list_memories(status="proposed",
                                            include_tests=True)
    assert rows and all(r["scope"] == "repo:myrepo" for r in rows)


def test_distill_explicit_scope_overrides_session_repo():
    _seed_session()
    _register_session_repo("sess-d", "myrepo")
    distill_session(memory.get_store(), "sess-d", scope="global",
                    llm=_llm(_PYTEST_ITEM))
    rows = memory.get_store().list_memories(status="proposed",
                                            include_tests=True)
    assert rows and all(r["scope"] == "global" for r in rows)
    # proposals stay out of recall until approved
    assert memory.recall("pytest ImportError venv", mode="fts",
                         include_tests=True) == []


def test_approval_makes_proposal_recallable():
    _seed_session()
    result = distill_session(memory.get_store(), "sess-d",
                             llm=_llm(_PYTEST_ITEM))
    for mid in result.memory_ids:
        memory.update(mid, status="active")
    hits = memory.recall("pytest ImportError", mode="fts",
                         include_tests=True)
    assert hits


def test_llm_distill_validates_structured_output():
    _seed_session()
    llm = StubLLM("""Here are the memories:
```json
[{"title": "Use the venv interpreter",
  "body": "Always run regin via .venv/bin/python; the system interpreter lacks the project's deps and fails with ImportError.",
  "kind": "gotcha", "importance": 0.6, "tags": ["venv", "interpreter"]}]
```""")
    result = distill_session(memory.get_store(), "sess-d", llm=llm)
    assert result.source == "llm"
    assert result.proposed == 1
    row = memory.get_store().list_memories(status="proposed",
                                           include_tests=True)[0]
    assert row["title"] == "Use the venv interpreter"
    assert row["kind"] == "gotcha"
    assert row["importance"] == 0.6  # the model's self-score is stored
    # provenance tags prepended, LLM tags preserved
    assert row["tags"] == ["distill", "llm", "venv", "interpreter"]
    # the prompt sets the bar, allows zero, demands JSON + abstraction,
    # surfaces the failure→fix + correction signals to the model, and
    # instructs the distiller to grep standing docs before proposing
    for needle in ("returning zero or one", "noise, not memory", "JSON array",
                   "running account", "Notable signals",
                   "doc_redundancy_check", "grep", "CLAUDE.md",
                   "doc-redundant", "docs are silent",
                   "git log", "git show", "commit sha"):
        assert needle in llm.prompts[0]


def test_llm_unparseable_output_proposes_nothing():
    """Unparseable LLM output is not a license to fall back to fabricated
    heuristics — it proposes nothing (the no-abstraction outcome)."""
    _seed_session()
    result = distill_session(memory.get_store(), "sess-d",
                             llm=StubLLM("- Use the venv interpreter\n"))
    assert result.source == "none"
    assert result.proposed == 0
    assert memory.get_store().list_memories(include_tests=True) == []


def test_llm_empty_array_is_an_affirmative_nothing():
    _seed_session()
    result = distill_session(memory.get_store(), "sess-d",
                             llm=StubLLM("[]"))
    assert result.source == "llm"
    assert result.proposed == 0  # the LLM judged there was nothing to keep


def test_llm_invalid_items_are_dropped():
    _seed_session()
    llm = StubLLM("""[
      {"body": "too short"},
      "not even a dict",
      {"title": "label", "body": "A long enough body that nonetheless states no rule.", "kind": "lesson"},
      {"title": "Read a file before Edit", "body": "A genuinely long enough body naming cli/regin.py and the exact ImportError symptom to be reusable.", "kind": "not-a-kind", "tags": ["a", "b", "c", "d"]}
    ]""")
    result = distill_session(memory.get_store(), "sess-d", llm=llm)
    # the no-rule "label" body is < 60 chars and dropped; only the last
    # item clears body + title length.
    assert result.proposed == 1
    row = memory.get_store().list_memories(status="proposed",
                                           include_tests=True)[0]
    assert row["title"] == "Read a file before Edit"
    assert row["kind"] == "lesson"            # unknown kind normalized
    assert len(row["tags"]) == 2 + 3          # provenance + capped LLM tags


def test_high_importance_self_score_auto_approves():
    _seed_session()
    result = distill_session(memory.get_store(), "sess-d", llm=_llm({
        "title": "Read a file before Edit",
        "body": ("Edit fails with 'file has not been read yet' unless the "
                 "file was Read earlier this session — Read before Edit."),
        "kind": "gotcha", "importance": 0.92, "tags": ["edit", "read"]}))
    assert result.approved == 1 and result.proposed == 0
    rows = memory.get_store().list_memories(include_tests=True)
    assert len(rows) == 1 and rows[0]["status"] == "active"
    # auto-approved memories are immediately recallable, no human step
    assert memory.recall("Edit file has not been read", mode="fts",
                         include_tests=True)


def test_low_importance_self_score_is_dropped():
    _seed_session()
    result = distill_session(memory.get_store(), "sess-d", llm=_llm({
        "title": "A marginal observation",
        "body": ("A marginal note that is not really worth keeping around "
                 "for future sessions at all."),
        "kind": "lesson", "importance": 0.1, "tags": ["meh"]}))
    assert result.dropped == 1
    assert result.proposed == 0 and result.approved == 0
    assert result.memory_ids == []


def test_empty_session_proposes_nothing():
    result = distill_session(memory.get_store(), "missing-session",
                             llm=_llm(_PYTEST_ITEM))
    assert result.proposed == 0


# ── Slice 1 of the grade→memory loop: the grade digest ──────────────

def _grade_payload():
    """A `{axis: grade_dict}` mirroring what grade_session persists, with a
    failing claim, a missing coverage item, a proxy source, and a wasted
    process span — one of each problem shape the digest renders."""
    return {
        "correctness": {
            "verdict": "needs_revision",
            "detail": {
                "claims": [
                    {"id": "c1", "normalized_text": "login.ts validates the JWT",
                     "referents": {"file": "src/auth/login.ts"},
                     "load_bearing": True},
                    {"id": "c2", "normalized_text": "an incidental aside",
                     "referents": {}, "load_bearing": False},
                    {"id": "c3", "normalized_text": "a grounded fact",
                     "referents": {}, "load_bearing": True},
                ],
                "verdicts": {
                    "c1": {"verdict": "UNGROUNDED",
                           "reason": "no Read span backs it"},
                    "c2": {"verdict": "UNGROUNDED", "reason": "noise"},
                    "c3": {"verdict": "GROUNDED", "reason": ""},
                    "c0": {"verdict": "UNGROUNDED", "reason": "aggregate"},
                },
                "checklist": [
                    {"item": "regression test added", "verdict": "MISSING",
                     "reason": "not addressed"},
                    {"item": "fix applied", "verdict": "COVERED", "reason": ""},
                ],
                "sources": [
                    {"source": "a blog post", "verdict": "PROXY", "reason": ""},
                    {"source": "the source file", "verdict": "AUTHORITATIVE",
                     "reason": ""},
                ],
            },
        },
        "process": {
            "verdict": "wasteful",
            "detail": {
                "tool_use": {"findings": [
                    {"span_id": "s1", "verdict": "WASTED",
                     "reason": "`cat foo` — output never used downstream"},
                    {"span_id": "s2", "verdict": "APPROPRIATE", "reason": ""},
                ]},
                "redundancy": {"redundant_reads": [{"path": "x"}],
                               "thrash_episodes": []},
                "reliability": {"ignored_feeding_claim": ["s9"]},
            },
        },
    }


def test_grade_digest_renders_only_problems():
    from lib.memory.distill import _grade_digest
    digest = _grade_digest(_grade_payload())
    present = [
        "automated grader flagged these problems",   # the abstract-a-rule framing
        "login.ts validates the JWT",                # failing claim text
        "src/auth/login.ts",                         # its referent
        "no Read span backs it",                     # the verdict reason
        "coverage MISSING: regression test added",
        "source PROXY: a blog post",
        "WASTED tool use", "1 redundant_reads",
        "ignored error(s) feed a claim",
    ]
    # grounded / non-load-bearing / c0 / covered / authoritative / appropriate
    # / empty-bucket all stay out — only real problems surface
    absent = ["a grounded fact", "an incidental aside", "fix applied",
              "AUTHORITATIVE", "thrash_episodes"]
    for needle in present:
        assert needle in digest, needle
    for needle in absent:
        assert needle not in digest, needle


def test_grade_digest_empty_when_clean():
    from lib.memory.distill import _grade_digest
    clean = {"correctness": {"verdict": "satisfied", "detail": {
        "claims": [{"id": "c1", "load_bearing": True}],
        "verdicts": {"c1": {"verdict": "GROUNDED"}},
        "checklist": [{"item": "x", "verdict": "COVERED"}], "sources": []}}}
    assert _grade_digest(clean) == ""
    assert _grade_digest(None) == ""
    assert _grade_digest({}) == ""


def test_grade_feeds_prompt_and_bonus_promotes_to_active():
    """The grade digest reaches the LLM, and importance_bonus lifts a
    mid-score draft over the auto-approve bar (0.75 + 0.15 ≥ 0.85)."""
    _seed_session()
    llm = _llm({
        "title": "Ground state claims against a Read before asserting",
        "body": ("Do not claim a file does X without a Read span backing it; "
                 "the grader marks unbacked state claims UNGROUNDED."),
        "kind": "lesson", "importance": 0.75, "tags": ["grounding"]})
    result = distill_session(memory.get_store(), "sess-d", llm=llm,
                             grade=_grade_payload(), importance_bonus=0.15)
    # the grader's findings rode into the prompt ahead of the trace
    assert "login.ts validates the JWT" in llm.prompts[0]
    assert "automated grader flagged" in llm.prompts[0]
    # +0.15 pushed 0.75 over the 0.85 auto-approve bar → active, not queued
    assert result.approved == 1 and result.proposed == 0
    row = memory.get_store().list_memories(status="active",
                                           include_tests=True)[0]
    assert row["importance"] == 0.9


# ── agentic prompt: self-fetch instructions, no embedded raw trace ───

def test_compose_prompt_is_agentic_and_embeds_no_raw_trace():
    """The distiller is told to fetch the trace itself; the high-signal
    grader findings ride in a tag, but the raw spans are NOT folded in —
    prompt size must not scale with the session length."""
    from lib.memory.distill import _compose_prompt

    spans = [{"name": "tool.Bash", "attrs": {"command_preview": f"cmd {i}"},
              "span_id": f"s{i}", "start_time": f"t{i}"}
             for i in range(300)]
    grade = {"correctness": {"verdict": "needs_revision", "detail": {
        "claims": [{"id": "c1", "type": "state", "load_bearing": True,
                    "normalized_text": "auth.py checks the token"}],
        "verdicts": {"c1": {"verdict": "UNGROUNDED", "reason": "no Read"}},
        "checklist": [], "sources": []}}}
    prompt = _compose_prompt("sess-xyz", spans, grade, ".venv/bin/python")

    present = [
        "<gather_evidence>",                              # self-fetch instructions
        "<session_id>sess-xyz</session_id>",              # parameterised trace id
        ".venv/bin/python cli/regin.py trace dump sess-xyz --index",
        "trace span sess-xyz <span_id>",
        "<grader_findings>", "auth.py checks the token",  # high-signal embed
        "<output_format>",
    ]
    # the 300 raw spans are NOT folded in — no preview leaks, no trace block
    absent = ["cmd 0", "cmd 299", "[SESSION TRACE"]
    for needle in present:
        assert needle in prompt, needle
    for needle in absent:
        assert needle not in prompt, needle
    # prompt size stays small regardless of session length
    assert len(prompt) < 6000


def test_compose_prompt_omits_empty_sections():
    """No grade and no notable signals → no hollow tags; the gather-evidence
    and output-format sections always anchor the prompt."""
    from lib.memory.distill import _compose_prompt

    spans = [{"name": "tool.Read", "attrs": {"file_path": "a.py"},
              "span_id": "s1", "start_time": "t1"}]
    prompt = _compose_prompt("sess-1", spans, None, ".venv/bin/python")
    assert "<grader_findings>" not in prompt
    assert "<notable_signals>" not in prompt
    assert "<gather_evidence>" in prompt and "<output_format>" in prompt


# ── dedup-at-write: reinforce instead of re-insert ──────────────────────

# A proposal body close enough to _PYTEST_ITEM to exceed dedup_text_threshold
# when paired with the same title — used to seed a near-duplicate scenario.
_PYTEST_ITEM_NEAR_DUP = {
    "title": "Use the venv interpreter for pytest",
    "body": ("Run pytest using .venv/bin/python; the system interpreter "
             "raises ImportError because it doesn't have the project deps."),
    "kind": "gotcha",
    "importance": 0.88,
    "tags": ["venv", "pytest"],
}

# A proposal that is clearly different (different domain entirely) and should
# always insert as a new row.
_DISTINCT_ITEM = {
    "title": "Always quote file paths with spaces in shell commands",
    "body": ("Shell glob expansion silently skips files with spaces unless "
             "the path is double-quoted; use double-quotes around all paths "
             "in Bash tool calls to avoid missing files."),
    "kind": "gotcha",
    "importance": 0.82,
    "tags": ["shell", "paths"],
}


def test_dedup_reinforces_instead_of_inserting():
    """A near-duplicate proposal reinforces the existing row rather than
    inserting a second one: row count stays the same, importance is bumped,
    a validation event is recorded, and result.reinforced == 1."""
    _seed_session("sess-dedup1")
    store = memory.get_store()
    # First distill — inserts the original row (auto-approved via importance=0.88)
    r1 = distill_session(store, "sess-dedup1", scope="global",
                         llm=_llm(_PYTEST_ITEM_NEAR_DUP))
    assert r1.approved == 1 and r1.reinforced == 0
    rows_after_first = store.list_memories(include_tests=True)
    assert len(rows_after_first) == 1
    original_importance = rows_after_first[0]["importance"]

    # Second distill — same session content, same near-dup proposal
    _seed_session("sess-dedup2")
    r2 = distill_session(store, "sess-dedup2", scope="global",
                         llm=_llm(_PYTEST_ITEM_NEAR_DUP))
    assert r2.reinforced == 1
    assert r2.approved == 0 and r2.proposed == 0

    rows_after_second = store.list_memories(include_tests=True)
    # No new row was inserted
    assert len(rows_after_second) == 1

    # Importance was bumped to max(existing, incoming) — never decreased
    new_importance = rows_after_second[0]["importance"]
    assert new_importance >= original_importance

    # A validation event was recorded for the reinforcement
    from lib.memory.models import MemoryValidation
    from lib.memory.engine import MemorySessionLocal
    from sqlmodel import select
    mid = rows_after_second[0]["id"]
    with MemorySessionLocal() as session:
        validations = session.exec(
            select(MemoryValidation)
            .where(MemoryValidation.memory_id == mid)
            .where(MemoryValidation.action == "reinforced")).all()
    assert len(validations) >= 1


def test_distinct_proposal_still_inserts():
    """A sufficiently different proposal is not treated as a duplicate and
    inserts as a new row regardless of what's already in the store."""
    _seed_session("sess-dedup3")
    store = memory.get_store()
    r1 = distill_session(store, "sess-dedup3", scope="global",
                         llm=_llm(_PYTEST_ITEM_NEAR_DUP))
    assert r1.approved == 1

    _seed_session("sess-dedup4")
    r2 = distill_session(store, "sess-dedup4", scope="global",
                         llm=_llm(_DISTINCT_ITEM))
    assert r2.reinforced == 0
    assert r2.approved + r2.proposed == 1   # inserted (not reinforced)

    rows = store.list_memories(include_tests=True)
    assert len(rows) == 2


def test_dedup_failure_degrades_to_insert(monkeypatch):
    """Any exception in the dedup path is swallowed and the proposal is
    written as a fresh insert — the dedup never blocks a write."""
    _seed_session("sess-dedup5")
    store = memory.get_store()

    # Patch _dedup_candidate to always raise
    import lib.memory.distill as distill_mod
    monkeypatch.setattr(distill_mod, "_dedup_candidate",
                        lambda *_a, **_kw: (_ for _ in ()).throw(
                            RuntimeError("simulated dedup failure")))

    r = distill_session(store, "sess-dedup5", scope="global",
                        llm=_llm(_PYTEST_ITEM_NEAR_DUP))
    # Degraded to plain insert — row written, reinforced stays 0
    assert r.reinforced == 0
    assert r.approved == 1
    assert len(store.list_memories(include_tests=True)) == 1


# ── Idempotency guard: skip when already distilled ──────────────────────────

def _idem1_after_two_runs():
    """Shared fixture: two distill_session calls on the same session.
    Returns (store, llm, r1, r2) for assertion helpers below."""
    _seed_session("sess-idem1")
    store = memory.get_store()
    llm = _llm(_PYTEST_ITEM_NEAR_DUP)
    r1 = distill_session(store, "sess-idem1", scope="global", llm=llm)
    r2 = distill_session(store, "sess-idem1", scope="global", llm=llm)
    return store, llm, r1, r2


def test_second_distill_returns_skipped_flag():
    """Second distill_session call sets skipped_already_distilled=True."""
    _store, _llm, _r1, r2 = _idem1_after_two_runs()
    assert r2.skipped_already_distilled is True


def test_second_distill_llm_not_invoked():
    """Second distill_session call must NOT invoke the LLM again."""
    _store, llm, _r1, _r2 = _idem1_after_two_runs()
    assert len(llm.prompts) == 1


def test_second_distill_no_new_rows():
    """Second distill_session call must not insert any new memory rows."""
    store, _llm, _r1, _r2 = _idem1_after_two_runs()
    assert len(store.list_memories(include_tests=True)) == 1


def test_second_distill_result_counts_zero():
    """Second distill_session call returns all-zero counters."""
    _store, _llm, _r1, r2 = _idem1_after_two_runs()
    assert r2.proposed == 0
    assert r2.approved == 0
    assert r2.dropped == 0
    assert r2.reinforced == 0
    assert r2.memory_ids == []


def test_send_to_user_lesson_does_not_trip_idempotency_guard():
    """A `send_to_user(type=lesson)` capture stamps the session id as
    `source_trace_id` (tagged `send_to_user`), but it is NOT a distill — so
    it must not make the session look already-distilled. Regression for the
    conflation bug where any row with the trace id permanently blocked
    distillation of every session that emitted a lesson."""
    _seed_session("sess-lesson")
    store = memory.get_store()
    # Simulate the lesson-capture hook writing a memory for this session.
    memory.remember("A reusable lesson body from send_to_user.",
                    kind="lesson", title="A reusable lesson",
                    tags=["send_to_user"], source_trace_id="sess-lesson")
    assert store.distilled_memories_from_trace("sess-lesson") == 0

    # The guard must NOT fire: distill runs and the LLM is invoked.
    llm = _llm(_PYTEST_ITEM)
    r = distill_session(store, "sess-lesson", scope="global", llm=llm)
    assert r.skipped_already_distilled is False
    assert len(llm.prompts) == 1
    # And the run leaves a distill-tagged row behind it.
    assert store.distilled_memories_from_trace("sess-lesson") == 1


def test_force_bypasses_idempotency_guard():
    """force=True re-runs the LLM even when memories for the trace exist.
    Dedup-at-write handles the resulting near-duplicate — result.reinforced
    records the dedup hit rather than inserting a second row."""
    _seed_session("sess-idem2")
    store = memory.get_store()
    llm = _llm(_PYTEST_ITEM_NEAR_DUP)

    # First run — inserts one row
    r1 = distill_session(store, "sess-idem2", scope="global", llm=llm)
    assert r1.approved == 1
    assert len(llm.prompts) == 1

    # Second run with force=True — LLM is invoked; dedup handles the duplicate
    r2 = distill_session(store, "sess-idem2", scope="global", llm=llm,
                         force=True)
    assert r2.skipped_already_distilled is False
    # LLM was called a second time
    assert len(llm.prompts) == 2
    # Dedup-at-write reinforced instead of inserting a duplicate row
    assert r2.reinforced == 1
    assert r2.approved == 0
    # Still only one row in the store
    assert len(store.list_memories(include_tests=True)) == 1


# ── at-write conflict resolution: supersede a contradicted memory ───────────


class _ScriptedLLM:
    """Returns the distill proposals JSON for the distill prompt, and a fixed
    verdict for the supersede-judgment prompt — told apart by a marker phrase
    only the latter contains. A single StubLLM can't serve both: it would feed
    the verdict word back as the proposals array (and vice-versa)."""

    def __init__(self, proposals_json, verdict="CONTRADICT"):
        self.proposals_json = proposals_json
        self.verdict = verdict
        self.prompts = []

    def complete(self, prompt, *, max_tokens=1024):
        self.prompts.append(prompt)
        if "CONTRADICT if incompatible" in prompt:
            return self.verdict
        return self.proposals_json


# The existing (about-to-be-stale) memory, seeded directly into the store.
_SUITE_OLD = {
    "title": "Run the suite with pytest directly",
    "body": ("Run regin's test suite by invoking pytest directly: `pytest -q` "
             "from the repo root picks up all tests."),
}
# The new proposal that makes _SUITE_OLD wrong. Lexical similarity to it is
# ~0.57 — inside the supersede band [0.5, 0.90): a contradiction, not a
# restatement (which would dedup) nor an unrelated row.
_SUITE_CONTRADICTION = {
    "title": "Never run the suite with pytest directly",
    "body": ("Do not invoke pytest directly for regin's suite; the bare "
             "command misconfigures paths. Use `.venv/bin/python -m pytest` "
             "from the repo root instead."),
    "kind": "gotcha", "importance": 0.9, "tags": ["pytest", "venv"],
}


def _seed_existing_memory(item):
    return memory.remember(item["body"], title=item["title"], status="active",
                          scope="global", is_test=True)


def test_contradicting_proposal_supersedes_existing():
    """A fresh proposal the LLM judges to CONTRADICT an existing memory retires
    the old row (status=retired, veracity=false, superseded_by) in the new
    row's favour — at write time, not at the next reflect."""
    old_id = _seed_existing_memory(_SUITE_OLD)
    _seed_session("sess-sup1")
    store = memory.get_store()
    llm = _ScriptedLLM(json.dumps([_SUITE_CONTRADICTION]), verdict="CONTRADICT")

    r = distill_session(store, "sess-sup1", scope="global", llm=llm)

    assert r.superseded == 1
    assert r.approved == 1            # the new row auto-approved (importance 0.9)
    old = store.get_dict(old_id)
    assert old["status"] == "retired" and old["veracity"] == "false"
    actives = [m for m in store.list_memories(include_tests=True)
               if m["status"] == "active"]
    assert len(actives) == 1
    assert old["superseded_by"] == actives[0]["id"]
    # the supersede judgment was actually consulted (gray-band gate passed it)
    assert any("CONTRADICT if incompatible" in p for p in llm.prompts)


def test_consistent_verdict_keeps_both():
    """A band candidate the LLM judges CONSISTENT is left alone: the new row
    inserts alongside the old one — supersede never retires on a non-verdict."""
    _seed_existing_memory(_SUITE_OLD)
    _seed_session("sess-sup2")
    store = memory.get_store()
    llm = _ScriptedLLM(json.dumps([_SUITE_CONTRADICTION]), verdict="CONSISTENT")

    r = distill_session(store, "sess-sup2", scope="global", llm=llm)

    assert r.superseded == 0
    actives = [m for m in store.list_memories(include_tests=True)
               if m["status"] == "active"]
    assert len(actives) == 2          # both kept
    # the candidate was still put to the LLM — proving the band gate let it
    # through and CONSISTENT (not the gate) is what spared the old row
    assert any("CONTRADICT if incompatible" in p for p in llm.prompts)


def test_supersede_disabled_keeps_both(monkeypatch):
    """With distill_supersede_on_conflict off, a contradiction never retires
    the old row and the LLM is never asked to judge one."""
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory,
                        "distill_supersede_on_conflict", False)
    _seed_existing_memory(_SUITE_OLD)
    _seed_session("sess-sup3")
    store = memory.get_store()
    llm = _ScriptedLLM(json.dumps([_SUITE_CONTRADICTION]), verdict="CONTRADICT")

    r = distill_session(store, "sess-sup3", scope="global", llm=llm)

    assert r.superseded == 0
    assert len([m for m in store.list_memories(include_tests=True)
                if m["status"] == "active"]) == 2
    assert not any("CONTRADICT if incompatible" in p for p in llm.prompts)


# ── slice 2: auto-file distilled memories under the meta-roots ──────────

_PREF_ITEM = {
    "title": "Prefer concise recommendation over option survey",
    "body": ("When reporting, give a single recommendation with a short why, "
             "not an exhaustive survey of every alternative considered."),
    "kind": "preference",
    "tags": ["communication"],
}
_PROC_ITEM = {
    "title": "Restart backend before Playwright E2E asserts",
    "body": ("Playwright reuseExistingServer keeps a stale Python on :8321; "
             "restart the backend after edits or E2E runs against old code."),
    "kind": "procedure",
    "tags": ["playwright"],
}


def test_distill_files_preference_and_procedure_under_meta_roots():
    """A distilled `preference` lands under `preferences`; a `procedure` under
    `skills` — the cheap deterministic auto-filing path."""
    _seed_session()
    store = memory.get_store()
    result = distill_session(store, "sess-d", llm=_llm(_PREF_ITEM, _PROC_ITEM))
    by_kind = {store.get_dict(mid)["kind"]: mid for mid in result.memory_ids}
    assert "preferences" in store.authoritative_topics_of(by_kind["preference"])
    assert "skills" in store.authoritative_topics_of(by_kind["procedure"])


def test_distill_meta_root_link_respects_flag(monkeypatch):
    """With `distill_link_meta_roots` off, nothing is filed."""
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "distill_link_meta_roots", False)
    _seed_session()
    store = memory.get_store()
    result = distill_session(store, "sess-d", llm=_llm(_PREF_ITEM))
    assert result.memory_ids
    assert store.authoritative_topics_of(result.memory_ids[0]) == []


def test_distill_other_kinds_are_not_auto_filed():
    """Only preference/procedure map to a bucket; a `gotcha` is left for the
    agentic classifier (no deterministic home)."""
    _seed_session()
    store = memory.get_store()
    result = distill_session(store, "sess-d", llm=_llm(_PYTEST_ITEM))  # gotcha
    assert result.memory_ids
    assert store.authoritative_topics_of(result.memory_ids[0]) == []
