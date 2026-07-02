"""Implicit capture: distill candidate memories from a finished session.

Reads a session's trace out of the *main* regin DB (`session_spans`) and
proposes memories into the memory DB. Proposals land with
`status='proposed'` — a human approves them in the Memory UI / CLI before
they participate in recall. That gate is what keeps the store from
filling with noise; distill never writes silently.

Distillation is LLM-only by contract: the model is what turns a session
into an *abstracted* rule. Deterministic heuristics still run, but only
to surface the highest-signal moments (failure→fix chains, user pushback)
at the top of the LLM input — they no longer fabricate proposals of their
own (that produced session-narrating "running account" noise). With no
LLMProvider configured, distill proposes nothing.

The distiller is **agentic** (`resolve_distiller` grants it the read-only
`trace dump`/`trace span` commands): rather than folding the whole session
into the prompt, it is handed the trace id plus the high-signal hints (the
grader's findings and the notable-signals digest) and fetches only the
spans it needs to make each memory concrete. Prompt size stays constant
regardless of session length — the same scaling fix the deep-tier judge
uses (`lib/grader/agentic.py`).

Each LLM-drafted proposal is self-scored (`importance` in [0,1]) and,
per the `agent_memory` thresholds, dropped (below the floor),
auto-approved (`status='active'`), or queued for human review
(`status='proposed'`). The approval gate keeps the store curated; the
floor + auto-approve band keep the human out of the obvious cases.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field

from lib.activity_log import get_activity_logger
from lib.memory.models import DISTILL_TAG, MemoryInput
from lib.memory.reflect import _text_similarity

log = get_activity_logger("memory")

_MAX_PROPOSALS = 10
_CORRECTION_MARKERS = (
    "no,", "no.", "that's wrong", "thats wrong", "not what i",
    "actually,", "don't ", "dont ", "stop ", "undo ", "revert ",
    "you should have", "wrong file", "wrong approach",
)


@dataclass
class DistillResult:
    trace_id: str
    proposed: int = 0       # left in 'proposed' status (human review queue)
    approved: int = 0       # auto-approved straight to 'active'
    dropped: int = 0        # self-scored below the importance floor
    reinforced: int = 0     # near-duplicate found; existing row reinforced
    superseded: int = 0     # contradicted an existing row; old one retired
    memory_ids: list[str] = dc_field(default_factory=list)
    source: str = "none"    # 'llm' once a provider drafts; 'none' otherwise
    skipped_already_distilled: bool = False  # guard fired; LLM not invoked


def _session_scope(trace_id: str) -> str:
    """`repo:<name>` for the session's primary registered repo, or
    'global' when the session isn't associated with one. This is what
    lets distilled memories carry their repo category without the caller
    having to know it."""
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT r.name FROM session_repos sr "
            "JOIN repos r ON r.id = sr.repo_id "
            "WHERE sr.trace_id = ? "
            "ORDER BY sr.is_primary DESC, sr.created_at ASC LIMIT 1",
            (trace_id,)).fetchone()
    finally:
        conn.close()
    return f"repo:{row['name']}" if row else "global"


def _session_spans(trace_id: str) -> list[dict]:
    """Tool/prompt spans for one session, oldest first, attrs decoded."""
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT name, span_id, attributes, status_code, start_time "
            "FROM session_spans WHERE trace_id = ? "
            "AND status_code != 'PENDING' ORDER BY start_time ASC",
            (trace_id,)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            attrs = json.loads(r["attributes"] or "{}")
        except (json.JSONDecodeError, ValueError):
            attrs = {}
        out.append({"name": r["name"], "span_id": r["span_id"],
                    "attrs": attrs, "start_time": r["start_time"]})
    return out


def _failure_target(attrs: dict) -> str:
    return attrs.get("file_path") or attrs.get("command_preview") or ""


def _failure_fix_chains(spans: list[dict]) -> list[tuple[dict, dict]]:
    """(failure span, fixing span) pairs: same tool + same target, fix
    strictly after the failure."""
    chains = []
    failures = [s for s in spans if s["name"] == "tool.failure"]
    for fail in failures:
        tool = fail["attrs"].get("tool_name")
        target = _failure_target(fail["attrs"])
        if not tool:
            continue
        for s in spans:
            if (s["start_time"] > fail["start_time"]
                    and s["name"] == f"tool.{tool}"
                    and _failure_target(s["attrs"]) == target):
                chains.append((fail, s))
                break
    return chains


def _correction_prompts(spans: list[dict]) -> list[dict]:
    out = []
    for s in spans:
        if s["name"] != "prompt":
            continue
        text = (s["attrs"].get("text") or "").strip().lower()
        if any(text.startswith(m) for m in _CORRECTION_MARKERS):
            out.append(s)
    return out


def _signal_digest(spans: list[dict]) -> str:
    """The highest-signal moments — failure→fix chains and user pushback —
    rendered as hints at the top of the LLM input so the model abstracts
    *these* into rules. The old heuristic path turned them directly into
    session-narrating proposals; now they only point the LLM at what to
    consider. Empty string when nothing notable fired."""
    lines = []
    for fail, _fix in _failure_fix_chains(spans):
        tool = fail["attrs"].get("tool_name") or "tool"
        target = _failure_target(fail["attrs"]) or "a target"
        error = str(fail["attrs"].get("error")
                    or fail["attrs"].get("status_message") or "failed")[:160]
        lines.append(f"- {tool} on {target} failed («{error}»), then a later "
                     f"{tool} on it succeeded. Capture the reusable cause/fix "
                     f"if there is one.")
    for prompt in _correction_prompts(spans):
        text = str(prompt["attrs"].get("text") or "").strip()[:200]
        lines.append(f"- User pushed back: «{text}». Capture the durable "
                     f"preference/rule behind it, not the literal words.")
    if not lines:
        return ""
    return ("Notable signals (high-priority candidates):\n"
            + "\n".join(lines) + "\n\n")


def _ungrounded_claim_line(claim: dict, v: dict) -> str:
    text = (claim.get("normalized_text") or claim.get("raw_text")
            or "").strip()[:180]
    ref = claim.get("referents") or {}
    ref_s = " ".join(str(x) for x in ref.values() if x)[:80]
    return (f"- claim {v.get('verdict')}: «{text}»"
            + (f" [{ref_s}]" if ref_s else "")
            + (f" — {v.get('reason')}" if v.get("reason") else ""))


def _ungrounded_lines(detail: dict) -> list[str]:
    by_id = {c.get("id"): c for c in (detail.get("claims") or [])}
    out: list[str] = []
    for cid, v in (detail.get("verdicts") or {}).items():
        claim = by_id.get(cid) or {}
        weak = v.get("verdict") != "GROUNDED" and cid != "c0"
        if weak and claim.get("load_bearing", True):
            out.append(_ungrounded_claim_line(claim, v))
    return out


def _coverage_lines(detail: dict) -> list[str]:
    return [f"- coverage {i.get('verdict')}: {str(i.get('item'))[:160]}"
            for i in (detail.get("checklist") or [])
            if i.get("verdict") in ("MISSING", "PARTIAL")]


def _source_lines(detail: dict) -> list[str]:
    return [f"- source {s.get('verdict')}: {str(s.get('source'))[:120]}"
            for s in (detail.get("sources") or [])
            if s.get("verdict") in ("PROXY", "UNVERIFIED")]


def _correctness_problems(detail: dict) -> list[str]:
    return (_ungrounded_lines(detail) + _coverage_lines(detail)
            + _source_lines(detail))


def _process_problems(detail: dict) -> list[str]:
    lines = [f"- {f.get('verdict')} tool use: {str(f.get('reason'))[:160]}"
             for f in (detail.get("tool_use") or {}).get("findings", [])
             if f.get("verdict") in ("SUBOPTIMAL", "WASTED")]
    lines += [f"- redundancy: {len(eps)} {kind}"
              for kind, eps in (detail.get("redundancy") or {}).items() if eps]
    feeding = (detail.get("reliability") or {}).get("ignored_feeding_claim")
    if feeding:
        lines.append(f"- {len(feeding)} ignored error(s) feed a claim")
    return lines


def _grade_problems(axis: str, detail: dict) -> list[str]:
    """The grader's *findings* for one axis, one line each — only the
    problems (the signal distill otherwise never sees). [] when clean."""
    if axis == "correctness":
        return _correctness_problems(detail)
    if axis == "process":
        return _process_problems(detail)
    return []


def _grade_digest(grade: "dict | None") -> str:
    """Render the grader's findings across axes as high-priority hints the
    LLM must turn into preventive rules. `grade` is `{axis: grade_dict}`
    (each with a `detail` blob). Empty string when nothing was flagged."""
    if not grade:
        return ""
    blocks: list[str] = []
    for axis, g in grade.items():
        detail = (g or {}).get("detail") or {}
        problems = _grade_problems(axis, detail)
        if problems:
            verdict = (g or {}).get("verdict", "?")
            blocks.append(f"{axis} graded '{verdict}':\n" + "\n".join(problems))
    if not blocks:
        return ""
    return ("The automated grader flagged these problems with this session "
            "— capture the durable rule that would have prevented each one "
            "(abstract it; do not restate the grader's verdict):\n"
            + "\n".join(blocks) + "\n\n")


def _clamp_importance(value) -> float:
    """Self-scored importance into [0,1]; 0.5 when absent or unparseable."""
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


# The distiller prompt (investigation instructions + output contract) now
# lives as the editable `memory-distill` surface
# (lib/prompts/surfaces/memory.py::_DEFAULT_BODY_DISTILL). The bar an entry
# must clear is deliberately spelled out there, and "return []" is legitimized:
# an open-ended "list up to N lessons" anchors the model to produce exactly N,
# padding with generic process advice that pollutes recall — selectivity over
# coverage. `_compose_prompt` below only wires the runtime context.
_BODY_MIN_CHARS = 60   # anything shorter is a slogan, not a memory
_TITLE_MIN_CHARS = 10  # the title must state the rule, not just label it


def _extract_json_array(answer: str):
    """Parse a JSON array out of model output, tolerating markdown fences
    and surrounding prose. Returns None when no array can be parsed."""
    text = re.sub(r"```(?:json)?", "", answer)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, list) else None


def _validated_proposal(item, trace_id: str) -> "MemoryInput | None":
    """One schema-checked proposal, or None when the item fails the
    contract (not a dict, body too short, or no rule-shaped title). A
    required, non-trivial title is what forces the model to state the
    rule instead of dumping an untitled running account."""
    if not isinstance(item, dict):
        return None
    body = str(item.get("body") or "").strip()
    title = str(item.get("title") or "").strip()
    if len(body) < _BODY_MIN_CHARS or len(title) < _TITLE_MIN_CHARS:
        return None
    tags = [str(t)[:40] for t in (item.get("tags") or [])
            if isinstance(t, (str, int))][:3]
    return MemoryInput(
        body=body[:2000],
        kind=str(item.get("kind") or "lesson"),  # store normalizes unknowns
        title=title[:120],
        tags=tags,
        importance=_clamp_importance(item.get("importance")),
        status="proposed",
        source_trace_id=trace_id,
    )


def _tagged(tag: str, body: str) -> str:
    """Wrap a non-empty digest in an XML tag; '' when the digest is empty
    so an absent section never leaves a hollow tag in the prompt."""
    body = body.rstrip()
    return f"<{tag}>\n{body}\n</{tag}>" if body else ""


def _block(tag: str) -> str:
    """A digest tag as a prompt block with its leading `\\n\\n` separator, or ''
    when the digest is empty — so an absent section leaves no blank gap and the
    composed prompt stays byte-identical to the old join-non-empty logic."""
    return f"\n\n{tag}" if tag else ""


def _compose_prompt(trace_id: str, spans: list[dict], grade: "dict | None",
                    python: str) -> str:
    """Assemble the agentic distill prompt via the editable `memory-distill`
    surface. The investigation instructions (the agent self-fetches the trace)
    and the output contract live in the surface body; this only wires the
    runtime context — the trace id, interpreter, and the high-signal hint blocks
    (grader findings + heuristic notable-signals). The raw trace is NOT embedded;
    the agent reads it on demand, so prompt size never scales with session
    length. A broken user edit degrades to the built-in default inside
    `render_surface`."""
    from lib.prompts import render_surface
    from lib.prompts.surfaces.memory import DISTILL_SURFACE_ID
    context = {
        "trace_id": trace_id,
        "python": python,
        "grader_block": _block(_tagged("grader_findings", _grade_digest(grade))),
        "notable_block": _block(_tagged("notable_signals", _signal_digest(spans))),
    }
    return render_surface(DISTILL_SURFACE_ID, context)


def _llm_proposals(trace_id: str, spans: list[dict], llm,
                   grade: "dict | None" = None,
                   python: str = ".venv/bin/python"
                   ) -> "list[MemoryInput] | None":
    """LLM-drafted proposals, schema-validated. None (no completion or
    unparseable output) tells the caller to propose nothing; a
    parsed-but-empty array is an affirmative 'nothing worth keeping'.

    The distiller is agentic: it investigates the trace itself via the
    granted `trace dump`/`trace span` commands. `grade` is the session's
    `{axis: grade_dict}` (with `detail`); its flagged problems are handed
    to the model as the highest-priority candidates to abstract a rule
    from. Evidence is fetched, not embedded."""
    answer = llm.complete(_compose_prompt(trace_id, spans, grade, python),
                          max_tokens=4096)
    if not answer:
        return None
    items = _extract_json_array(answer)
    if items is None:
        log.error("distill_llm_output_unparseable", trace_id=trace_id)
        return None
    proposals = [p for p in (_validated_proposal(i, trace_id) for i in items)
                 if p is not None]
    dropped = len(items) - len(proposals)
    if dropped:
        log.write("distill_items_rejected", trace_id=trace_id, dropped=dropped)
    return proposals[:_MAX_PROPOSALS]


def _finalize_status(importance: float, cfg) -> "str | None":
    """Map a self-scored importance to a write status: None (drop) below
    the floor, 'active' (auto-approve) at/above the bar, else 'proposed'
    (human review queue)."""
    if importance < cfg.distill_min_importance:
        return None
    if importance >= cfg.auto_approve_importance:
        return "active"
    return "proposed"


def _dedup_candidate(store, p: MemoryInput, scope: str, cfg):
    """Return an existing active memory that is a near-duplicate of `p`,
    or None. Uses FTS recall (text-only; no embedder needed) then confirms
    with the same normalized text-similarity reflect uses. Scope-aware:
    candidates must be in the same scope or 'global'."""
    query = f"{p.title or ''} {p.body}"
    try:
        hits = store.recall(query, top_k=5, scope=scope, mode="fts",
                            reinforce=False, include_tests=True)
    except Exception:
        log.error("distill_dedup_recall_failed", exc_info=True)
        return None
    p_text = f"{p.title or ''}\n{p.body}"
    for hit in hits:
        mem = hit.memory
        if mem.get("status") == "retired":
            continue
        candidate_text = f"{mem.get('title') or ''}\n{mem.get('body', '')}"
        if _text_similarity(p_text, candidate_text) >= cfg.dedup_text_threshold:
            return mem
    return None


def _reinforce_existing(store, mem: dict, p: MemoryInput,
                        result: DistillResult) -> None:
    """Bump importance toward max(existing, incoming) and record a
    validation event. Never decreases existing importance."""
    bumped = max(mem.get("importance") or 0.0, p.importance)
    store.update(mem["id"], importance=bumped)
    store.record_validation(mem["id"], validator="distill",
                            action="reinforced")
    result.reinforced += 1
    log.write("distill_dedup_reinforced", memory_id=mem["id"],
              importance=bumped)


# Lexical band a candidate must fall in to be judged a contradiction at
# write time. Below the floor it is unrelated; at/above the dedup threshold
# it is a restatement (`_dedup_candidate`'s job). The band between is the
# same-topic-different-claim zone — and gating on it also caps the
# (subprocess-backed) LLM calls to where a contradiction is plausible.
_SUPERSEDE_SIM_FLOOR = 0.5
_SUPERSEDE_MAX_CHECKS = 3   # at most this many LLM judgments per proposal


def _llm_says_supersedes(llm, p: MemoryInput, mem: dict) -> bool:
    """True iff the new proposal `p` makes a claim INCOMPATIBLE with the
    existing memory `mem` about the same thing — so the old one is now wrong
    and should be retired in favour of the new. One-word answer; anything but
    an explicit CONTRADICT means keep both (never retire on a guess)."""
    prompt = (
        "An EXISTING memory and a NEW memory from coding sessions follow. "
        "Does the NEW one make a claim INCOMPATIBLE with the EXISTING one "
        "about the same thing (so the EXISTING one is now wrong)? Answer with "
        "exactly one word — CONTRADICT if incompatible, or CONSISTENT "
        "otherwise.\n\n"
        f"EXISTING: {mem.get('title') or ''}\n{(mem.get('body') or '')[:1200]}"
        f"\n\nNEW: {p.title or ''}\n{p.body[:1200]}\n"
    )
    answer = llm.complete(prompt, max_tokens=8)
    return bool(answer) and "CONTRADICT" in answer.upper()


def _safe_supersede_recall(store, p: MemoryInput, scope: str) -> list:
    """FTS recall for the supersede check, degrading to [] on any failure
    so a recall error never blocks the write."""
    query = f"{p.title or ''} {p.body}"
    try:
        return store.recall(query, top_k=5, scope=scope, mode="fts",
                            reinforce=False, include_tests=True)
    except Exception:
        log.error("distill_supersede_recall_failed", exc_info=True)
        return []


def _in_conflict_band(p_text: str, mem: dict, cfg) -> bool:
    """True iff `mem` is an active candidate whose lexical similarity to the
    proposal sits in the contradiction band [floor, dedup_threshold)."""
    if mem.get("status") == "retired":
        return False
    candidate_text = f"{mem.get('title') or ''}\n{mem.get('body', '')}"
    sim = _text_similarity(p_text, candidate_text)
    return _SUPERSEDE_SIM_FLOOR <= sim < cfg.dedup_text_threshold


def _supersede_candidate(store, p: MemoryInput, scope: str, cfg, llm):
    """An existing active memory the new proposal CONTRADICTS — same topic,
    incompatible claim — to be retired in its favour, or None. Distinct from
    `_dedup_candidate`: that reinforces near-identical restatements; this
    replaces a now-wrong memory. Gated on an LLM (we never guess a
    contradiction) and on `distill_supersede_on_conflict`; only candidates in
    the lexical gray band are put to the LLM (see `_SUPERSEDE_SIM_FLOOR`)."""
    if llm is None or not cfg.distill_supersede_on_conflict:
        return None
    p_text = f"{p.title or ''}\n{p.body}"
    checks = 0
    for hit in _safe_supersede_recall(store, p, scope):
        if not _in_conflict_band(p_text, hit.memory, cfg):
            continue
        if checks >= _SUPERSEDE_MAX_CHECKS:
            break
        checks += 1
        if _llm_says_supersedes(llm, p, hit.memory):
            return hit.memory
    return None


def _write_proposal(store, p: MemoryInput, result: DistillResult,
                    superseded: "dict | None" = None) -> None:
    """Persist `p` and advance the result counters. When `superseded` is
    given, the new row retires it (status=retired, veracity=false,
    superseded_by) — the at-write contradiction resolution."""
    mid = store.remember(p)
    result.memory_ids.append(mid)
    if superseded is not None:
        store.update(superseded["id"], status="retired", veracity="false",
                     superseded_by=mid)
        store.record_validation(superseded["id"], validator="distill",
                                action="superseded",
                                note=f"contradicted by {mid}")
        result.superseded += 1
        log.write("distill_superseded", old=superseded["id"], new=mid)
    if p.status == "active":
        store.record_validation(mid, validator="distill",
                                action="auto_approved")
        result.approved += 1
    else:
        result.proposed += 1


# Deterministic kind → global meta-root bucket. The cheap auto-filing path:
# a distilled `preference`/`procedure` memory lands under a navigable home
# without waiting for the agentic `link-topics` classifier (which routes to a
# precise leaf). Other kinds (lesson/gotcha/fact) are left for the classifier.
_KIND_META_ROOT = {"preference": "preferences", "procedure": "skills"}


def _link_meta_root(store, memory_id: str, kind: str) -> None:
    """File a freshly written distilled memory under its kind's meta-root,
    best-effort — a link failure must never break the distill write."""
    node = _KIND_META_ROOT.get(kind)
    if node is None:
        return
    try:
        store.link_authoritative_topic(memory_id, node, source="distill")
    except Exception:
        log.error("distill_meta_root_link_failed", memory_id=memory_id,
                  node=node, exc_info=True)


def _store_proposal(store, p: MemoryInput, scope: str, cfg,
                    result: DistillResult, importance_bonus: float = 0.0,
                    llm=None) -> None:
    # A grader-flagged session is independent corroboration that the
    # problem is real, so nudge those drafts toward auto-approval.
    if importance_bonus:
        p.importance = _clamp_importance(p.importance + importance_bonus)
    status = _finalize_status(p.importance, cfg)
    if status is None:
        result.dropped += 1
        return
    p.status = status
    p.scope = scope
    p.tags = [DISTILL_TAG, "llm", *p.tags]
    try:
        dup = _dedup_candidate(store, p, scope, cfg)
    except Exception:
        log.error("distill_dedup_check_failed", exc_info=True)
        dup = None
    if dup is not None:
        _reinforce_existing(store, dup, p, result)
        return
    try:
        superseded = _supersede_candidate(store, p, scope, cfg, llm)
    except Exception:
        log.error("distill_supersede_check_failed", exc_info=True)
        superseded = None
    _write_proposal(store, p, result, superseded)
    # Auto-file the just-written row under its kind's meta-root. Only reached
    # on a genuine write (the dedup/reinforce path returns above), so the last
    # id appended by `_write_proposal` is this proposal's new memory.
    if cfg.distill_link_meta_roots and result.memory_ids:
        _link_meta_root(store, result.memory_ids[-1], p.kind)


def distill_session(store, trace_id: str, *, scope: "str | None" = None,
                    llm=None, grade: "dict | None" = None,
                    importance_bonus: float = 0.0,
                    python: str = ".venv/bin/python",
                    force: bool = False) -> DistillResult:
    """Propose memories from one finished session. With an LLM the drafts
    are abstracted, self-scored, and either dropped / queued / auto-
    approved per the `agent_memory` thresholds; without one, distill
    proposes nothing — heuristics can detect signal but can't abstract it
    into a reusable rule, and an un-abstracted memory is the running-
    account noise we refuse to write. `scope=None` resolves the session's
    own repo via `session_repos`; pass an explicit scope to override.

    The distiller is agentic: granted the read-only trace commands (see
    `resolve_distiller`), it fetches the session's own spans to make each
    memory concrete instead of having the trace embedded in its prompt.
    `grade` is the session's `{axis: grade_dict}` (with `detail`): the
    grader's flagged problems are fed to the LLM as the highest-priority
    candidates, and `importance_bonus` nudges those drafts toward
    auto-approval — the grade→memory loop's entry point. `python` is the
    interpreter the agent should invoke regin's CLI with.

    Idempotency guard: if a prior distill row exists for this trace — a row
    carrying both `source_trace_id == trace_id` and the `DISTILL_TAG`
    provenance marker — the LLM is NOT re-invoked and
    `result.skipped_already_distilled` is set True. The marker is what keeps
    the guard from tripping on a `send_to_user(type=lesson)` capture, which
    also stamps the session id as `source_trace_id` but tags itself
    `send_to_user`. Pass `force=True` to bypass this check for deliberate
    re-runs (e.g. manual `regin memory distill --force`). The dedup-at-write
    logic handles any resulting near-duplicates.

    Subtlety: a session whose every proposal was either dropped or reinforced
    into existing rows leaves NO rows with this source_trace_id, so the guard
    would not fire and the LLM would re-run. That is acceptable — the prior
    run's signal already exists in reinforced rows — and documenting it here
    is the right honesty trade-off versus adding a separate distill-log table."""
    from lib.settings import settings
    result = DistillResult(trace_id=trace_id)
    if not force and store.distilled_memories_from_trace(trace_id) > 0:
        log.write("distill_skipped_already_distilled", trace_id=trace_id)
        result.skipped_already_distilled = True
        return result
    spans = _session_spans(trace_id)
    if not spans:
        return result
    proposals = (_llm_proposals(trace_id, spans, llm, grade, python)
                 if llm is not None else None)
    if proposals is None:
        log.write("distill_skipped_no_llm", trace_id=trace_id)
        return result
    result.source = "llm"
    cfg = settings.agent_memory
    resolved_scope = scope if scope is not None else _session_scope(trace_id)
    for p in proposals:
        _store_proposal(store, p, resolved_scope, cfg, result,
                        importance_bonus, llm)
    log.write("session_distilled", trace_id=trace_id, proposed=result.proposed,
              approved=result.approved, dropped=result.dropped,
              scope=resolved_scope)
    return result


__all__ = ["distill_session", "DistillResult"]
