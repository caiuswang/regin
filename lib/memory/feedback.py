"""Inject→usefulness feedback — the missing half of the recall loop.

Auto-inject shows a memory speculatively but never learns whether it
helped: a memory injected into 50 sessions and ignored every time keeps
its quality score, and a memory that demonstrably guided the work gets no
credit. The only earned signal today is reinforce-on-resurface (the same
memory matching twice in one session, `reinforce_resurfaced`).

`score_injection_usefulness` closes that gap with a *deterministic*,
LLM-free verdict per injected memory:

  1. Pull the concrete *referents* out of each injected memory — file
     paths, backtick-quoted identifiers, CLI commands. A memory with no
     such referents can't be checked against the trace, so it abstains
     ('no_referents'): no signal, neither credit nor penalty.
  2. Look at the session's own tool spans that fired **after** the memory
     was injected (`injection_events.injected_at`). A referent appearing
     before the injection moment can't have been caused by it, so the
     ordering gate is load-bearing.
  3. If any referent appears in a later span's `file_path` /
     `command_preview` / `text` attrs → 'engaged' (the work touched what
     the memory talked about); otherwise 'ignored'.

Persistence mirrors the validators reflect/distill already write:
'engaged' records a validation and nudges importance up; 'ignored'
records a validation only (no per-event penalty — *decay* is reflect's
job, gated on a run of ignores). This is the positive half of the loop;
`reflect._decay_chronically_ignored` is the negative half.

Wired after a real grading run (next to `distill_on_fail`), gated on
`settings.agent_memory.feedback_on_grade`, and best-effort: a feedback
failure must never fail the grade.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field as dc_field

from lib.activity_log import get_activity_logger
from lib.settings import settings

log = get_activity_logger("memory")

# Importance reward for an engaged memory, and its ceiling. Small and
# capped: one engagement is weak evidence; it should nudge, not promote.
_ENGAGED_BONUS = 0.05
_IMPORTANCE_CAP = 1.0

# Below this many active (non-test) memories the referent document-frequency
# is too noisy to tell a specific referent from a corpus-common one, so idf
# weighting stays off and the verdict falls back to the binary "any referent
# matched" rule. Mirrors store._IDF_MIN_CORPUS (the inject gate's guard).
_IDF_MIN_CORPUS = 20

# Referent extractors over the memory's title+body.
#   - a path-ish token: contains a slash, or ends in a known source ext.
#   - a backtick-quoted identifier: `like_this`.
# A bare word is deliberately NOT a referent: matching memories on common
# English would make every memory look "engaged".
_BACKTICK = re.compile(r"`([^`]+)`")
_TOKEN = re.compile(r"[^\s`'\"(),;]+")
_PATH_EXT = (".py", ".vue", ".md", ".js", ".ts", ".sql", ".json", ".sh",
             ".toml", ".yaml", ".yml", ".css", ".html")
# Concrete referents shorter than this are too generic to be evidence
# (e.g. a 2-char identifier collides with everything).
_MIN_REFERENT_CHARS = 4


@dataclass
class FeedbackResult:
    """Outcome of one `score_injection_usefulness` pass."""

    trace_id: str
    engaged: int = 0        # >=1 referent appeared in a post-injection span
    ignored: int = 0        # injected, no referent overlap
    no_referents: int = 0   # nothing concrete to check — abstained
    engaged_ids: list[str] = dc_field(default_factory=list)


def _is_pathish(token: str) -> bool:
    return "/" in token or token.endswith(_PATH_EXT)


def _referents(title: "str | None", body: str) -> set[str]:
    """Concrete, checkable tokens from a memory: backtick-quoted
    identifiers, file paths, and CLI-command-shaped words. Lowercased for
    case-insensitive containment against span text. A memory with an empty
    set abstains — there is nothing to verify against the trace."""
    text = f"{title or ''}\n{body}"
    out: set[str] = set()
    for ident in _BACKTICK.findall(text):
        ident = ident.strip()
        if len(ident) >= _MIN_REFERENT_CHARS:
            out.add(ident.lower())
    for token in _TOKEN.findall(text):
        if len(token) >= _MIN_REFERENT_CHARS and _is_pathish(token):
            out.add(token.lower())
    return out


def _idf_weight(ref: str, n: int, df: "dict[str, int]") -> float:
    """Normalised idf in [0, 1] for one referent: 1.0 when it appears in a
    single session (maximally specific — its reappearance downstream is strong
    evidence the memory steered the work), → 0 as it saturates the session
    corpus (e.g. `cli/regin.py`, in most sessions regardless). A referent
    absent from the cache (a new memory not yet scanned) is given the benefit
    of the doubt as unique (df=1). `n >= _IDF_MIN_CORPUS` is guaranteed by
    `_engagement_idf`, so `log(n) > 0`."""
    d = min(df.get(ref, 0) or 1, n)
    return max(0.0, min(1.0, math.log(n / d) / math.log(n)))


def _engagement_idf(store=None) -> "tuple[int, dict[str, int]] | None":
    """The (corpus_sessions, referent→session-df) pair that idf-weights the
    verdict, or None to fall back to the binary rule. Reads the cached
    `referent_session_df` table (built by `rebuild_session_referent_df`) — it
    never scans the trace DB itself, so scoring stays cheap. None when idf
    weighting is disabled (`engagement_idf_min_weight` <= 0), the cache is
    empty (never built — e.g. an isolated test DB), or its corpus is below
    `_IDF_MIN_CORPUS` sessions; each preserves the binary behaviour exactly."""
    if settings.agent_memory.engagement_idf_min_weight <= 0:
        return None
    from sqlmodel import select

    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import ReferentSessionDF

    with MemorySessionLocal() as session:
        rows = session.exec(
            select(ReferentSessionDF.referent, ReferentSessionDF.df,
                   ReferentSessionDF.corpus_sessions)).all()
    if not rows:
        return None
    n = rows[0][2]
    if n < _IDF_MIN_CORPUS:
        return None
    return n, {ref: d for ref, d, _ in rows}


def _span_haystack(attrs: dict) -> str:
    """The span text a referent could appear in: the file it touched, the
    command it ran, or its prompt/preview text — lowercased once."""
    parts = [attrs.get("file_path"), attrs.get("command_preview"),
             attrs.get("text"), attrs.get("command")]
    return " ".join(str(p) for p in parts if p).lower()


def _post_injection_spans(trace_id: str, injected_at: str) -> list[str]:
    """Lowercased haystacks for the session's non-pending spans that
    started strictly after `injected_at`. Re-implements distill's
    `_session_spans` query locally (the concurrent agent owns that file),
    narrowed to what feedback needs: start_time ordering + attr text."""
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT attributes FROM session_spans "
            "WHERE trace_id = ? AND status_code != 'PENDING' "
            "AND start_time > ? ORDER BY start_time ASC",
            (trace_id, injected_at)).fetchall()
    finally:
        conn.close()
    out: list[str] = []
    for r in rows:
        try:
            attrs = json.loads(r["attributes"] or "{}")
        except (json.JSONDecodeError, ValueError):
            attrs = {}
        out.append(_span_haystack(attrs))
    return out


def _active_referent_vocab() -> "set[str]":
    """Every referent across active, non-test memories — the only tokens whose
    session document frequency the engagement verdict can ever need."""
    from sqlmodel import select

    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import Memory

    with MemorySessionLocal() as session:
        rows = session.exec(
            select(Memory.title, Memory.body)
            .where(Memory.status == "active", Memory.is_test == 0)).all()
    vocab: set[str] = set()
    for title, body in rows:
        vocab |= _referents(title, body or "")
    return vocab


def _injection_session_ids() -> "list[str]":
    """Distinct sessions that ever received an auto-inject — the corpus over
    which referent ubiquity is measured (the population we score against)."""
    from sqlmodel import select

    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import InjectionEvent

    with MemorySessionLocal() as session:
        return list(session.exec(
            select(InjectionEvent.session_id).distinct()).all())


def _session_haystacks(session_ids: "list[str]"):
    """Yield (trace_id, haystack) for every non-pending span of `session_ids`,
    in one batched read of the trace DB. The caller scans it for referents."""
    if not session_ids:
        return
    from lib.orm.engine import get_connection
    placeholders = ",".join("?" * len(session_ids))
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT trace_id, attributes FROM session_spans "
            f"WHERE trace_id IN ({placeholders}) "
            "AND status_code != 'PENDING'", tuple(session_ids)).fetchall()
    finally:
        conn.close()
    for r in rows:
        try:
            attrs = json.loads(r["attributes"] or "{}")
        except (json.JSONDecodeError, ValueError):
            attrs = {}
        yield r["trace_id"], _span_haystack(attrs)


def _session_referent_df(vocab: "set[str]", sessions: "list[str]") -> "dict[str, int]":
    """Distinct-session document frequency for each referent in `vocab`: how
    many of `sessions` have at least one span whose text contains it."""
    seen: dict[str, set[str]] = {ref: set() for ref in vocab}
    if not vocab:
        return {}
    for trace_id, haystack in _session_haystacks(sessions):
        for ref in vocab:
            if ref in haystack:
                seen[ref].add(trace_id)
    return {ref: len(s) for ref, s in seen.items()}


def _write_session_referent_df(df: "dict[str, int]", n: int) -> None:
    """Replace the cached `referent_session_df` with a fresh snapshot."""
    from sqlmodel import select

    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import ReferentSessionDF

    now = _now_iso()
    with MemorySessionLocal() as session:
        for row in session.exec(select(ReferentSessionDF)).all():
            session.delete(row)
        for ref, d in df.items():
            session.add(ReferentSessionDF(
                referent=ref, df=d, corpus_sessions=n, computed_at=now))
        session.commit()


def rebuild_session_referent_df(store=None) -> int:
    """Recompute and cache the session-span document frequency of every active
    memory's referents (one bounded scan of the trace DB), so the idf-weighted
    engagement verdict can read it cheaply. Returns the referent count cached.

    Best run periodically — wired into the reflect sweep next to
    `score_pending_sessions`, since both the active vocab and the session
    corpus drift slowly. `engagement_idf_min_weight=0` skips the scan."""
    if settings.agent_memory.engagement_idf_min_weight <= 0:
        return 0
    vocab = _active_referent_vocab()
    sessions = _injection_session_ids()
    df = _session_referent_df(vocab, sessions)
    _write_session_referent_df(df, len(sessions))
    log.write("session_referent_df_rebuilt",
              referents=len(df), sessions=len(sessions))
    return len(df)


def _classify(trace_id: str, mem: dict, injected_at: str,
              idf: "tuple[int, dict[str, int]] | None") -> "tuple[str, bool | None]":
    """Return (verdict, matched) for one injected memory.

    verdict ∈ {'no_referents', 'engaged', 'ignored'}; `matched` is None for
    no_referents, else True when *any* referent (pre-idf) appeared in a
    post-injection span. The two diverge only under idf: a match on solely
    corpus-saturating referents (`cli/regin.py`) scores 'ignored' yet
    matched=True — a *soft* ignore the decay gate treats as generic contact,
    not evidence of uselessness. With `idf` None the rule is binary: any match
    → engaged. The matched referents' summed normalised idf must clear
    `engagement_idf_min_weight` for an idf-mode 'engaged'."""
    referents = _referents(mem.get("title"), mem.get("body") or "")
    if not referents:
        return "no_referents", None
    spans = _post_injection_spans(trace_id, injected_at)
    matched = {ref for ref in referents
               if any(ref in haystack for haystack in spans)}
    if not matched:
        return "ignored", False
    if idf is None:  # small corpus / disabled → any match is engagement
        return "engaged", True
    n, df = idf
    weight = sum(_idf_weight(ref, n, df) for ref in matched)
    if weight >= settings.agent_memory.engagement_idf_min_weight:
        return "engaged", True
    return "ignored", True  # soft ignore: matched, but only generic referents


def _injection_events(store, trace_id: str) -> list[tuple[str, str, "str | None"]]:
    """(memory_id, injected_at, query) for every *unscored* memory auto-injected
    into the session, oldest first. Read straight off the model so feedback
    carries the injection timestamp the ordering gate needs (the store's
    `injected_memory_ids` returns ids only) plus the recall query a hard-ignore
    verdict turns into a negative exemplar. The `scored_at IS NULL` filter
    makes scoring idempotent at the event level: a re-grade (or the pending
    sweep overlapping grade-time) never re-judges or double-rewards an event
    already stamped."""
    from sqlmodel import select

    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import InjectionEvent

    with MemorySessionLocal() as session:
        rows = session.exec(
            select(InjectionEvent.memory_id, InjectionEvent.injected_at,
                   InjectionEvent.query)
            .where(InjectionEvent.session_id == trace_id,
                   InjectionEvent.scored_at.is_(None))
            .order_by(InjectionEvent.injected_at.asc())).all()
    return [(mid, at, q) for mid, at, q in rows]


def _stamp_event(trace_id: str, memory_id: str, engaged: "int | None",
                 matched: "int | None") -> None:
    """Record the verdict on the (uncapped) injection event: `engaged` 1/0 for
    engaged/ignored (NULL for a no_referents abstain), `matched` 1/0 for
    whether any referent appeared downstream (NULL for abstain), and
    `scored_at` always set so the event is never re-judged. The per-memory
    rates (`Store.engagement_counts` / `engagement_match_counts`) read these."""
    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import InjectionEvent

    with MemorySessionLocal() as session:
        row = session.get(InjectionEvent, (trace_id, memory_id))
        if row is None:
            return
        row.engaged = engaged
        row.matched = matched
        row.scored_at = _now_iso()
        session.add(row)
        session.commit()


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat()


def _reward_engaged(store, memory_id: str, mem: dict,
                    result: FeedbackResult, reward_importance: bool) -> None:
    if reward_importance:
        bumped = min(_IMPORTANCE_CAP,
                     (mem.get("importance") or 0.0) + _ENGAGED_BONUS)
        store.update(memory_id, importance=bumped)
    store.record_validation(memory_id, validator="feedback", action="engaged")
    result.engaged += 1
    result.engaged_ids.append(memory_id)


def _record_verdict(store, trace_id: str, mem: dict, verdict: str,
                    matched: "bool | None", result: FeedbackResult,
                    reward_importance: bool) -> None:
    """Persist one memory's verdict, stamping the event row in every case so
    it is never re-judged. 'engaged' validates (and bumps importance only
    when `reward_importance`); 'ignored' validates only (no per-event
    penalty — decay is reflect's job) and records `matched` so the decay gate
    can distinguish a soft from a hard ignore; 'no_referents' abstains (event
    stamped scored, but `engaged`/`matched` left NULL → no signal)."""
    if verdict == "no_referents":
        result.no_referents += 1
        _stamp_event(trace_id, mem["id"], None, None)
        return
    if verdict == "engaged":
        _reward_engaged(store, mem["id"], mem, result, reward_importance)
        _stamp_event(trace_id, mem["id"], 1, 1)
    else:
        store.record_validation(mem["id"], validator="feedback",
                                action="ignored")
        result.ignored += 1
        _stamp_event(trace_id, mem["id"], 0, 1 if matched else 0)


def score_injection_usefulness(trace_id: str, store=None, *,
                               reward_importance: bool = True,
                               idf: "object" = "auto") -> FeedbackResult:
    """Score whether the memories auto-injected into `trace_id` engaged the
    session's actual work, and persist the verdicts.

    Deterministic and LLM-free: for each *unscored* injected memory, extract
    its concrete referents (paths, backtick identifiers, commands) and check
    whether any appears in a tool span that fired *after* the injection
    moment. Engaged → validation (+ importance bump when `reward_importance`);
    ignored → validation only; no checkable referents → abstain. Every event
    is stamped so it is judged once. The grade→memory loop's positive signal;
    `reflect` supplies the negative one.

    `reward_importance=False` is for the bulk pending sweep: it records the
    engaged-rate signal without nudging importance, so densifying the scoring
    across hundreds of historical injects can't inflate the importance axis.

    `idf` weights the verdict by referent specificity (see `_verdict`); the
    "auto" sentinel resolves it once per call via `_engagement_idf`. The
    pending sweep passes a shared map so the corpus df is scanned once for the
    whole batch, not once per session."""
    if store is None:
        import lib.memory as memory
        store = memory.get_store()
    if idf == "auto":
        idf = _engagement_idf(store)
    result = FeedbackResult(trace_id=trace_id)
    events = _injection_events(store, trace_id)
    negatives: list[tuple[str, str]] = []
    positives: list[tuple[str, str]] = []
    for memory_id, injected_at, query in events:
        mem = store.get_dict(memory_id)
        if mem is None:  # forgotten between injection and scoring — skip
            continue
        verdict, matched = _classify(trace_id, mem, injected_at, idf)
        _record_verdict(store, trace_id, mem, verdict, matched, result,
                        reward_importance)
        # A *hard* ignore (no referent matched) is the negative signal; an
        # engaged inject is the positive one. Both record the firing query as a
        # query-local exemplar so future similar prompts re-rank this memory,
        # without touching its stored importance.
        if verdict == "ignored" and matched is False and query:
            negatives.append((memory_id, query))
        elif verdict == "engaged" and query:
            positives.append((memory_id, query))
    _record_exemplars(store, trace_id, negatives, positives)
    log.write("injection_usefulness_scored", trace_id=trace_id,
              engaged=result.engaged, ignored=result.ignored,
              no_referents=result.no_referents)
    return result


def _record_exemplars(store, trace_id: str,
                      negatives: "list[tuple[str, str]]",
                      positives: "list[tuple[str, str]]") -> None:
    """Persist captured query exemplars, each direction gated on its own weight.
    Best-effort: an exemplar we can't embed/store must never fail the grade."""
    cfg = settings.agent_memory
    try:
        if negatives and cfg.negative_demotion_weight > 0:
            store.add_query_negatives(trace_id, negatives)
        if positives and cfg.positive_boost_weight > 0:
            store.add_query_positives(trace_id, positives)
    except Exception:  # noqa: BLE001 — feedback is best-effort
        log.error("exemplar_write_failed", exc_info=True)


def _pending_sessions(lag_minutes: int) -> list[str]:
    """Session ids with at least one *finished* unscored injection event —
    `injected_at` older than `lag_minutes`, so the post-injection spans the
    verdict needs have already landed. Scoring a still-live session would
    judge memories against an incomplete trace and manufacture false
    'ignored' verdicts, so the lag is load-bearing."""
    from datetime import datetime, timedelta

    from sqlmodel import select

    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import InjectionEvent

    cutoff = (datetime.now() - timedelta(minutes=lag_minutes)).isoformat()
    with MemorySessionLocal() as session:
        rows = session.exec(
            select(InjectionEvent.session_id)
            .where(InjectionEvent.scored_at.is_(None),
                   InjectionEvent.injected_at < cutoff)
            .distinct()).all()
    return list(rows)


def score_pending_sessions(store=None, *, lag_minutes: "int | None" = None,
                           max_sessions: int = 200) -> FeedbackResult:
    """Densify the positive half of the loop: score engagement for every
    finished session whose injects were never scored (the common case — no
    grade ever ran). Validation-only (no importance bump). Returns a single
    `FeedbackResult` aggregating all sessions swept. Best-effort per session:
    one session's failure must not abort the sweep."""
    if store is None:
        import lib.memory as memory
        store = memory.get_store()
    if lag_minutes is None:
        lag_minutes = settings.agent_memory.feedback_lag_minutes
    agg = FeedbackResult(trace_id="<pending-sweep>")
    idf = _engagement_idf(store)  # one corpus df scan for the whole sweep
    sessions = _pending_sessions(lag_minutes)[:max_sessions]
    for trace_id in sessions:
        try:
            r = score_injection_usefulness(trace_id, store,
                                           reward_importance=False, idf=idf)
        except Exception:  # noqa: BLE001 — one bad session can't stop the sweep
            log.error("pending_session_scoring_failed", exc_info=True)
            continue
        agg.engaged += r.engaged
        agg.ignored += r.ignored
        agg.no_referents += r.no_referents
        agg.engaged_ids.extend(r.engaged_ids)
    log.write("pending_sessions_scored", sessions=len(sessions),
              engaged=agg.engaged, ignored=agg.ignored,
              no_referents=agg.no_referents)
    return agg


__all__ = ["score_injection_usefulness", "score_pending_sessions",
           "rebuild_session_referent_df", "FeedbackResult"]
