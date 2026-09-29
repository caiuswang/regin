"""Write-side span ingest + materialization for trace data.

Writes the span ingest path (the ~60-line ON CONFLICT DO UPDATE in
`_SESSIONS_UPSERT_SQL`). `materialize_session` re-runs the projection
(graft + widen) and writes the result back to the DB. `get_connection` is
imported lazily so tests can monkey-patch `lib.orm.engine.get_connection`.

Per-turn usage, per-tool token attribution and the statusline snapshot
live in `ingest_usage`; the per-session counter buckets in
`session_buckets`; the append-only span-row writes in `span_rows`.
"""

from __future__ import annotations

from lib.trace.pending_spans import is_pending_span_id
from lib.trace.session_tags import parse_prompt_tags
from lib.trace.trace_service.ingest_usage import ingest_tool_attribution
from lib.trace.trace_service.session_buckets import (
    _TITLE_MAX_CHARS,
    _span_counter_buckets,
)
from lib.trace.trace_service.span_rows import (
    _detect_existing_spans,
    _insert_span_row,
    _trace_log,
)


# ── Session projection materialize ──────────────────────────

def materialize_session(trace_id: str) -> dict:
    """Persist the orphan-graft + envelope-widen projection to the DB.

    Wrapped in BEGIN IMMEDIATE so fetch + multiple updates form one
    atomic unit. Raises on any DB error — caller turns it into a 500.

    Also refreshes the `sessions.active_work_ms` aggregate so the list
    view stays in sync with the materialised projection.
    """
    from lib.orm.engine import get_connection
    from lib.trace.projection import (
        _compute_active_work_ms, _fetch_spans, _graft_orphans,
        _persist_projection, _widen_envelopes,
    )

    conn = get_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        raw = _fetch_spans(conn, trace_id)
        grafted = _graft_orphans(raw)
        widened = _widen_envelopes(grafted)
        updated = _persist_projection(conn, trace_id, raw, widened)
        active_ms = _compute_active_work_ms(widened)
        conn.execute(
            "UPDATE sessions SET active_work_ms = ? WHERE trace_id = ?",
            (active_ms, trace_id),
        )
        _refresh_group_span_count(conn, trace_id)
        conn.commit()
        updated['active_work_ms'] = active_ms
        _trace_log().write(
            "session_projection_materialized",
            trace_id=trace_id, active_work_ms=active_ms,
            span_count=len(raw),
        )
        return updated
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _refresh_group_span_count(conn, trace_id) -> None:
    """Re-point an aliased session's `span_count` at what its trace renders.

    The counter is maintained per-`trace_id` and incrementally, so for a
    regin-launched run it counts only the writer that happened to file under
    that id — while the trace view serves the two merged. Left alone the list
    would claim one number and the trace show another, with nothing to explain
    the gap.

    A no-op for an ordinary session — one indexed lookup on a tiny table, then
    return — so the 99% case pays almost nothing and cannot be perturbed. Only
    the canonical row is touched; the alias row keeps its own counters and is
    hidden from the list anyway.
    """
    from lib.trace.alias import trace_group
    from lib.trace.merge import merge_spans
    from lib.trace.projection import _fetch_spans_for_group

    group = trace_group(conn, trace_id)
    if len(group) < 2:
        return
    merged = merge_spans(_fetch_spans_for_group(conn, group))
    conn.execute("UPDATE sessions SET span_count = ? WHERE trace_id = ?",
                 (len(merged), group[0]))


def _refresh_active_work_ms(conn, trace_ids) -> None:
    """Recompute and persist `sessions.active_work_ms` for each trace.

    Re-runs graft + widen on all spans in the trace so the value matches
    what the detail view computes from the projected tree. Skipped for
    traces that have no row yet (the upsert running concurrently in the
    same transaction will create it; we get there first only when the
    very first span lands and the row is committed by the upsert before
    this fires).
    """
    from lib.trace.projection import (
        _compute_active_work_ms, _fetch_spans,
        _graft_orphans, _widen_envelopes,
    )

    for tid in trace_ids:
        if not tid:
            continue
        raw = _fetch_spans(conn, tid)
        if not raw:
            continue
        widened = _widen_envelopes(_graft_orphans(raw))
        conn.execute(
            "UPDATE sessions SET active_work_ms = ? WHERE trace_id = ?",
            (_compute_active_work_ms(widened), tid),
        )


# ── Session-span ingest ──────────────────────────────────────

_SESSIONS_UPSERT_SQL = """
    INSERT INTO sessions (
        trace_id, title, title_source,
        status, last_start_at, ended_at, ended_reason,
        started_at, last_seen,
        span_count, skill_reads, file_edits, rule_checks,
        plan_enters, prompts, tool_calls, is_test, test_name,
        agent_type, model, cwd
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(trace_id) DO UPDATE SET
        started_at   = MIN(sessions.started_at, excluded.started_at),
        last_seen    = MAX(sessions.last_seen,  excluded.last_seen),
        span_count   = sessions.span_count   + excluded.span_count,
        skill_reads  = sessions.skill_reads  + excluded.skill_reads,
        file_edits   = sessions.file_edits   + excluded.file_edits,
        rule_checks  = sessions.rule_checks  + excluded.rule_checks,
        plan_enters  = sessions.plan_enters  + excluded.plan_enters,
        prompts      = sessions.prompts      + excluded.prompts,
        tool_calls   = sessions.tool_calls   + excluded.tool_calls,
        is_test      = MAX(sessions.is_test, excluded.is_test),
        test_name    = COALESCE(sessions.test_name, excluded.test_name),
        -- Title precedence: user > user_rename > claude_ai_title > first_prompt.
        --   user         — set via the regin API (no UI yet); never auto-replaced
        --   user_rename  — written by Claude `/rename` (`custom-title` line)
        --   claude_ai_title — Claude's auto-generated `ai-title` line; latest wins
        --   first_prompt — derived from the earliest prompt span; fill-only
        title        = CASE
            WHEN sessions.title_source = 'user' THEN sessions.title
            WHEN excluded.title_source = 'user' THEN excluded.title
            WHEN excluded.title_source = 'user_rename' THEN excluded.title
            WHEN sessions.title_source = 'user_rename' THEN sessions.title
            WHEN excluded.title_source = 'claude_ai_title' THEN excluded.title
            WHEN sessions.title_source = 'claude_ai_title' THEN sessions.title
            WHEN sessions.title IS NULL AND excluded.title IS NOT NULL
                THEN excluded.title
            ELSE sessions.title END,
        title_source = CASE
            WHEN sessions.title_source = 'user' THEN sessions.title_source
            WHEN excluded.title_source = 'user' THEN excluded.title_source
            WHEN excluded.title_source = 'user_rename' THEN excluded.title_source
            WHEN sessions.title_source = 'user_rename' THEN sessions.title_source
            WHEN excluded.title_source = 'claude_ai_title' THEN excluded.title_source
            WHEN sessions.title_source = 'claude_ai_title' THEN sessions.title_source
            WHEN sessions.title IS NULL AND excluded.title IS NOT NULL
                THEN excluded.title_source
            ELSE sessions.title_source END,
        last_start_at = COALESCE(MAX(sessions.last_start_at, excluded.last_start_at),
                                 sessions.last_start_at, excluded.last_start_at),
        ended_at      = COALESCE(MAX(sessions.ended_at, excluded.ended_at),
                                 sessions.ended_at, excluded.ended_at),
        ended_reason  = CASE
            WHEN excluded.ended_at IS NOT NULL
                 AND (sessions.ended_at IS NULL
                      OR excluded.ended_at > sessions.ended_at)
                THEN excluded.ended_reason
            ELSE sessions.ended_reason END,
        status        = CASE
            WHEN COALESCE(MAX(sessions.ended_at, excluded.ended_at),
                          sessions.ended_at, excluded.ended_at) IS NOT NULL
                 AND (COALESCE(MAX(sessions.last_start_at, excluded.last_start_at),
                               sessions.last_start_at, excluded.last_start_at) IS NULL
                      OR COALESCE(MAX(sessions.ended_at, excluded.ended_at),
                                  sessions.ended_at, excluded.ended_at)
                          >= COALESCE(MAX(sessions.last_start_at, excluded.last_start_at),
                                      sessions.last_start_at, excluded.last_start_at))
                THEN 'ended'
            WHEN COALESCE(MAX(sessions.last_start_at, excluded.last_start_at),
                          sessions.last_start_at, excluded.last_start_at) IS NOT NULL
                THEN 'active'
            ELSE sessions.status END,
        -- Preserve variant-bracketed ids (e.g. claude-opus-4-7[1m]) from
        -- SessionStart; the transcript-backed `turn` span only carries
        -- the bare base, so don't let a new batch downgrade. Also
        -- reject the placeholder `<synthetic>` (session init /
        -- /compact markers) — it must never overwrite a real id.
        -- Preserve variant-bracketed ids (e.g. claude-opus-4-7[1m]) from
        -- SessionStart; the transcript-backed `turn` span only carries
        -- the bare base, so don't let a new batch downgrade. Also
        -- reject the placeholder `<synthetic>` (session init /
        -- /compact markers) — it must never overwrite a real id.
        agent_type    = COALESCE(sessions.agent_type, excluded.agent_type),
        model         = CASE
            WHEN excluded.model IS NULL THEN sessions.model
            WHEN excluded.model = '<synthetic>'
                 AND sessions.model IS NOT NULL
                 AND sessions.model != '<synthetic>' THEN sessions.model
            WHEN sessions.model IS NULL THEN excluded.model
            WHEN sessions.model LIKE excluded.model || '[%' THEN sessions.model
            ELSE excluded.model END,
        -- Starting cwd: fill-only. The earliest session.start sets it and
        -- a later batch must not clobber it (a /add-dir or cd doesn't
        -- change where the session began).
        cwd           = COALESCE(sessions.cwd, excluded.cwd)
"""


# High-signal span names for repo membership. Reads and Bash are
# deliberately excluded so an incidental cross-repo read never tags a
# session as multi-repo (see `SessionRepo` model docstring).
_REPO_CWD_NAMES = frozenset({'session.start', 'cwd.changed'})
_REPO_EDIT_NAMES = frozenset({'tool.Edit', 'tool.Write', 'tool.apply_patch'})

_SESSION_REPOS_UPSERT_SQL = """
    INSERT INTO session_repos (trace_id, repo_id, is_primary)
    VALUES (?, ?, ?)
    ON CONFLICT(trace_id, repo_id) DO UPDATE SET
        is_primary = MAX(session_repos.is_primary, excluded.is_primary)
"""


def _repo_signal_path(name: str, attrs: dict) -> tuple:
    """Return `(path, is_primary)` for a high-signal span, else `(None, 0)`.

    `session.start` carries the starting cwd (primary); `cwd.changed`
    carries a walked-into cwd; edit spans carry the mutated file path.
    """
    if name == 'session.start':
        return attrs.get('cwd'), 1
    if name == 'cwd.changed':
        return attrs.get('cwd'), 0
    if name in _REPO_EDIT_NAMES:
        return attrs.get('file_path'), 0
    return None, 0


def _active_repos_normalized():
    """Active registered repos, pre-normalized for prefix matching.

    Loaded once per batch; an empty result short-circuits the resolver so
    installs with no registered repos pay nothing.
    """
    from sqlmodel import select as _sel

    from lib.orm import SessionLocal
    from lib.orm.models import Repo
    from lib.rule_engines.repo_scope import normalize_repos

    with SessionLocal() as s:
        repos = s.exec(_sel(Repo).where(Repo.is_active == 1)).all()
    return normalize_repos(repos)


def _span_repo_signal(span: dict, attrs, norm) -> tuple | None:
    """((trace_id, repo_id), is_primary) for a span that points into a
    registered repo, else None."""
    name = span.get('name') or ''
    if name not in _REPO_CWD_NAMES and name not in _REPO_EDIT_NAMES:
        return None
    from lib.rule_engines.repo_scope import repo_for_path_norm

    path, primary = _repo_signal_path(name, attrs)
    tid = span.get('trace_id')
    if not path or tid is None:
        return None
    repo = repo_for_path_norm(path, norm)
    if repo is None:
        return None
    return (tid, repo.id), primary


def _resolve_session_repos(conn, normalised, duplicates) -> None:
    """Tag each session in the batch with the registered repos it touched.

    Membership rule lives in the `SessionRepo` model docstring. Delicate
    by design: the repo set is loaded+normalized once, the bulk of spans
    short-circuit on a single name-set check, and writes are bounded by
    the number of distinct repos per session.
    """
    norm = _active_repos_normalized()
    if not norm:
        return
    found: dict = {}
    for span, attrs in normalised:
        if (span.get('trace_id'), span.get('span_id')) in duplicates:
            continue
        hit = _span_repo_signal(span, attrs, norm)
        if hit is not None:
            key, primary = hit
            found[key] = max(found.get(key, 0), primary)
    for (tid, repo_id), is_primary in found.items():
        conn.execute(_SESSION_REPOS_UPSERT_SQL, (tid, repo_id, is_primary))


def _counter_buckets_excl_pending(normalised, existing_set) -> dict:
    """`_span_counter_buckets` over only the non-pending spans, so transient
    placeholders never advance the session aggregates."""
    counted = [sa for sa in normalised
               if not is_pending_span_id(sa[0].get('span_id'))]
    return _span_counter_buckets(counted, existing_set)


def _resolve_session_status(b: dict) -> str | None:
    """Derive the session status from a counter bucket."""
    if b['ended_at'] and (b['last_start_at'] is None
                          or b['ended_at'] >= b['last_start_at']):
        return 'ended'
    if b['last_start_at']:
        return 'active'
    return None


def _resolve_session_title(b: dict) -> tuple[str | None, str | None]:
    """Derive (title, title_source) from a counter bucket."""
    if b['live_title']:
        return b['live_title'], b['live_title_source']
    if b['title']:
        return b['title'], 'first_prompt'
    return None, None


# Span names that prove the agent actually did something after a
# `session.start` — as opposed to the bare start-only footprint a resume
# *preview* leaves. Kimi fires SessionStart for every session it renders
# in the resume picker, so a start alone is not evidence the session is
# live again.
_OPERATION_SPAN_NAMES = ('prompt', 'turn', 'assistant_response',
                         'assistant.thinking')

_OPERATION_SINCE_RESTART_SQL = f"""
    SELECT 1 FROM session_spans
     WHERE trace_id = ?
       AND start_time >= ?
       AND (name IN ({','.join('?' * len(_OPERATION_SPAN_NAMES))})
            OR name LIKE 'tool.%'
            OR name LIKE 'pre_tool.%')
     LIMIT 1
"""

# Providers whose CLI emits a SessionStart for a session it merely RENDERS
# in its resume picker. Everyone else's SessionStart means the session is
# genuinely live from that instant, so holding it back — even for the one
# batch before its first prompt lands — would show a running session as
# ended in the sessions list and the live picker.
_RESUME_PREVIEW_PROVIDERS = ('kimi',)

_RESTART_CANDIDATES_SQL = """
    SELECT trace_id, last_start_at, agent_type
      FROM sessions
     WHERE trace_id IN ({placeholders})
       AND status = 'active'
       AND ended_at IS NOT NULL
       AND last_start_at IS NOT NULL
"""


def _previews_resumed_sessions(agent_type: object) -> bool:
    from lib.providers.registry import canonical_agent_kind
    return canonical_agent_kind(agent_type) in _RESUME_PREVIEW_PROVIDERS


def _restart_candidates(conn, trace_ids) -> list:
    """The batch's sessions that the upsert just flipped (or held) 'active'
    while carrying an older `ended_at` — the rows a restart reconcile can
    apply to. One query for the whole batch; the common batch returns
    nothing and no per-trace work follows."""
    ids = list(trace_ids)
    if not ids:
        return []
    sql = _RESTART_CANDIDATES_SQL.format(
        placeholders=','.join('?' * len(ids)))
    return conn.execute(sql, ids).fetchall()


def _reconcile_restart_ended_marker(conn, trace_ids) -> None:
    """Settle the end marker on sessions a restart just flipped 'active'.

    The upsert derives status from `last_start_at` vs `ended_at` alone and
    keeps `ended_at` sticky (MAX of old/new), so without this pass an
    exited-then-resumed session carries a stale `ended_at` forever and every
    read path that keys off it (trace header Live pill, the trace view's
    poll-stop) keeps reporting a running session as ended. Two outcomes:

    - GENUINE restart — a trusted provider's SessionStart (everyone outside
      `_RESUME_PREVIEW_PROVIDERS`), or a previewing provider's start that
      real operation spans have since backed: CLEAR `ended_at` /
      `ended_reason`, so the bare marker is truthful again.
    - BARE PREVIEW — Kimi fires SessionStart for every session it renders
      in its resume picker, so a start alone is not evidence the session is
      live again: hold status at 'ended' AND keep `ended_at`. The candidate
      query re-selects the row on every later batch that touches it, so the
      first real operation span clears the marker in that batch.

    Activity is measured from `last_start_at`, not from `ended_at`: a
    transcript-scanned `turn` routinely lands a few ms AFTER the SessionEnd
    span, and that trailing turn belongs to the run that just finished, not
    to the restart.
    """
    for row in _restart_candidates(conn, trace_ids):
        if _previews_resumed_sessions(row['agent_type']):
            hit = conn.execute(
                _OPERATION_SINCE_RESTART_SQL,
                (row['trace_id'], row['last_start_at'], *_OPERATION_SPAN_NAMES),
            ).fetchone()
            if hit is None:
                conn.execute(
                    "UPDATE sessions SET status = 'ended' WHERE trace_id = ?",
                    (row['trace_id'],))
                continue
        conn.execute(
            "UPDATE sessions SET ended_at = NULL, ended_reason = NULL "
            "WHERE trace_id = ?",
            (row['trace_id'],))


def _upsert_session_counters(conn, buckets: dict) -> None:
    """Apply incremental sessions-table counter upserts for each trace."""
    for tid, b in buckets.items():
        new_status = _resolve_session_status(b)
        title_val, title_src = _resolve_session_title(b)
        conn.execute(_SESSIONS_UPSERT_SQL, (
            tid, title_val, title_src,
            new_status, b['last_start_at'], b['ended_at'], b['ended_reason'],
            b['started_at'], b['last_seen'],
            b['span_count'], b['skill_reads'], b['file_edits'],
            b['rule_checks'], b['plan_enters'], b['prompts'],
            b['tool_calls'], b['is_test'], b['test_name'],
            b['agent_type'], b['model'], b['cwd'],
        ))
    _reconcile_restart_ended_marker(conn, buckets.keys())


def _stored_surface_tags(conn, surface_id: str) -> "list | None":
    """The stored `prompt_templates` row's tags as a list, or None when there
    is no row (caller then falls back to the registry default). Returns None on
    any DB error — e.g. the `tags` column not yet migrated — so tagging
    degrades to the default rather than failing ingest."""
    import json as _json
    try:
        row = conn.execute(
            "SELECT tags FROM prompt_templates WHERE slug = ?",
            (surface_id,)).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    raw = row[0]
    if not raw:
        return []
    try:
        value = _json.loads(raw)
    except (ValueError, TypeError):
        return []
    return [v for v in value if isinstance(v, str)]


def _surface_tags(conn, surface_id: object) -> list[str]:
    """The normalized custom tags a prompt surface's runs self-apply.

    The link between prompt management and session tags. Reads the STORED
    prompt-template row's `tags` — the editable override set in the
    /prompt-templates UI — and falls back to the surface's `PromptSurface.tags`
    registry default only when no row exists yet, mirroring how
    `render_surface` prefers the stored body. An existing row with an empty
    list is honored (the user cleared the tags), not overridden. Every slug is
    re-normalized so a stale/hand-edited value can't inject a bad or
    builtin-colliding tag. Unknown surface / un-migrated column → [].
    """
    if not isinstance(surface_id, str) or not surface_id:
        return []
    from lib.trace.session_tags import normalize_custom_slug
    stored = _stored_surface_tags(conn, surface_id)
    if stored is None:
        from lib.prompts.registry import get_surface
        surface = get_surface(surface_id)
        stored = list(surface.tags) if surface else []
    out: list[str] = []
    for raw in stored:
        slug = normalize_custom_slug(raw)
        if slug and slug not in out:
            out.append(slug)
    return out


def _upsert_auto_tags(conn, tid: str, tags: list[str]) -> None:
    """Insert `tags` as source='auto' session_tags for `tid`. Shared by every
    `_stamp_*_origins` stamper; callers are responsible for the
    first-sighting (`existing_set`) guard before calling this, since that
    guard's shape (which attribute names the surface) differs per stamper."""
    from lib.trace.session_tags import upsert_auto_tags
    upsert_auto_tags(conn, tid, tags)


def _stamp_llm_stage_origins(conn, normalised: list[tuple],
                             existing_set: set) -> None:
    """Mark sessions spawned by a regin LLM stage on the *origin* axis, and
    auto-tag them by surface.

    `ExternalAgentLLM` tags its subprocess env with the surface id; the
    SessionStart hook copies it onto the `session.start` span as
    `llm_surface`. Mirrors `workflow_ingest._set_session_origin`: `origin`
    records what KIND of row this is, orthogonal to `agent_type`, so the
    Sessions list can filter these runs like captured workflows.

    Regin's own spawned agents never carry a `regin-tags:` prompt marker, so
    the first sighting of the `session.start` span also self-applies the
    source='auto' custom tags the run's **prompt surface declares** (via
    `PromptSurface.tags` in the prompt registry) — the tag config lives with
    the prompt, not derived blindly from the surface id, so prompt management
    decides what a surface's sessions are tagged with. A surface that declares
    no tags is left on the origin axis only. First-sighting-guarded (via
    `existing_set`) so a surface tag the user removed isn't resurrected on
    re-ingest. The common batch (no session.start at all) exits on the cheap
    name scan.
    """
    if not any(span.get('name') == 'session.start' for span, _ in normalised):
        return
    stamped: set = set()
    for span, attrs in normalised:
        tid = span.get('trace_id')
        surface = attrs.get('llm_surface')
        if not (span.get('name') == 'session.start' and surface
                and tid is not None):
            continue
        if tid not in stamped:
            stamped.add(tid)
            conn.execute(
                "UPDATE sessions SET origin = 'llm-stage' WHERE trace_id = ?",
                (tid,))
        if (tid, span.get('span_id')) not in existing_set:
            _upsert_auto_tags(conn, tid, _surface_tags(conn, surface))


def _stamp_topic_proposal_origins(conn, normalised: list[tuple],
                                  existing_set: set) -> None:
    """Mark regin-spawned topic-proposal sessions on the *origin* axis, and
    auto-tag them by the drafting surface.

    `proposal_external._emit` tags every proposal span (including
    `session.start`) with `agent_type='topic-proposal-agent'`. Mirrors
    `_stamp_llm_stage_origins`: `origin` records what KIND of row this is,
    so the builtin `topic-proposal` category derives from a single axis
    (origin) rather than sniffing `agent_type` at read time. Unlike the
    llm-stage surfaces, drafting has no `llm_surface` attr to disambiguate
    (its agent isn't spawned through `ExternalAgentLLM`) — but there is only
    ONE drafting surface, so its tags are looked up once per call (not once
    per span — the surface can't vary within one call) and memoized in
    `drafting_tags`. First-sighting-guarded via `existing_set`, same as
    `_stamp_llm_stage_origins`. The common batch (no session.start) exits on
    the cheap name scan.
    """
    if not any(span.get('name') == 'session.start' for span, _ in normalised):
        return
    stamped: set = set()
    drafting_tags: "list[str] | None" = None
    for span, attrs in normalised:
        tid = span.get('trace_id')
        is_drafting_start = (span.get('name') == 'session.start'
                             and attrs.get('agent_type') == 'topic-proposal-agent'
                             and tid is not None)
        if not is_drafting_start:
            continue
        if tid not in stamped:
            stamped.add(tid)
            conn.execute(
                "UPDATE sessions SET origin = 'topic-proposal' WHERE trace_id = ?",
                (tid,))
        if (tid, span.get('span_id')) not in existing_set:
            if drafting_tags is None:
                from lib.prompts.surfaces.drafting import SURFACE_ID as drafting_surface_id
                drafting_tags = _surface_tags(conn, drafting_surface_id)
            _upsert_auto_tags(conn, tid, drafting_tags)


def _stamp_prompt_tags(conn, normalised: list[tuple], existing_set: set) -> None:
    """Auto-tag sessions from a `regin-tags:` marker on a prompt's first line.

    Only NEW `prompt` spans (absent from `existing_set`) are parsed, so a tag
    the user later removed is never resurrected when the same transcript is
    re-scanned. The flip side: a prompt whose FIRST-ever ingest carried no
    `text` is never re-parsed on a later re-ingest that does — unreachable in
    practice (a `prompt` span carries its full text from the first sighting).
    Rows are written with source='auto'; `ON CONFLICT DO NOTHING` leaves a
    same-slug manual tag (source='manual') untouched. The common batch (no
    prompt span) exits on the cheap name scan without touching attrs.
    """
    if not any(span.get('name') == 'prompt' for span, _ in normalised):
        return
    for span, attrs in normalised:
        if span.get('name') != 'prompt':
            continue
        tid = span.get('trace_id')
        if tid is None or (tid, span.get('span_id')) in existing_set:
            continue
        for slug in parse_prompt_tags(attrs.get('text')):
            _upsert_auto_tags(conn, tid, [slug])


def _refresh_server_side_peaks(conn, normalised: list[tuple]) -> None:
    """Recompute peak_main_context_tokens for any trace this batch touched
    with server-side spans (advisor and similar sub-calls). Spans can arrive
    after the matching turn_usage row has already had its aggregates derived,
    so without this symmetric refresh peak_main would stay stale at peak_full.
    """
    server_side_traces = {
        s.get('trace_id')
        for s, attrs in normalised
        if attrs.get('server_side') is True
    }
    server_side_traces.discard(None)
    for tid in server_side_traces:
        row = conn.execute(
            """
            SELECT MAX(tu.context_used_tokens)
              FROM turn_usage tu
             WHERE tu.trace_id = ?
               AND NOT EXISTS (
                   SELECT 1 FROM session_spans ss
                    WHERE ss.trace_id = tu.trace_id
                      AND COALESCE(ss.turn_uuid,
                                   json_extract(ss.attributes, '$.turn_uuid'))
                          = tu.turn_uuid
                      AND json_extract(ss.attributes, '$.server_side') = 1
               )
            """,
            (tid,),
        ).fetchone()
        conn.execute(
            "UPDATE sessions SET peak_main_context_tokens = ? WHERE trace_id = ?",
            (row[0] if row else None, tid),
        )


def ingest_session_spans(normalised: list[tuple]) -> tuple[int, int]:
    """Persist a batch of normalised spans in one transaction.

    `normalised` is the pre-validated output of `_validate_span` —
    each entry is `(span_dict, attrs_dict)`. Returns
    `(ingested_count, skipped_duplicates)`. Raises on any DB error
    — caller surfaces 500.

    Maintains `sessions` counters incrementally via the module-level
    `_SESSIONS_UPSERT_SQL` so the list view doesn't have to GROUP BY
    every time it's rendered.
    """
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        existing_set, skipped = _detect_existing_spans(conn, normalised)

        conn.execute('BEGIN IMMEDIATE')
        for span, attrs in normalised:
            _insert_span_row(conn, span, attrs)

        # Append-only: placeholders/pending rows are NOT retired here. They
        # coexist with their resolved counterparts and the serve-time merge
        # (lib/trace/merge.py) drops the superseded ones at read time.

        # Pending placeholders carry reserved id prefixes and are skipped by
        # _counter_buckets_excl_pending, so they never advance any aggregate.
        buckets = _counter_buckets_excl_pending(normalised, existing_set)
        _upsert_session_counters(conn, buckets)
        _stamp_llm_stage_origins(conn, normalised, existing_set)
        _stamp_topic_proposal_origins(conn, normalised, existing_set)
        _stamp_prompt_tags(conn, normalised, existing_set)
        _refresh_server_side_peaks(conn, normalised)

        # Refresh the active-work aggregate for every trace this batch
        # touched. Re-projecting per batch keeps the list view honest
        # without waiting for materialize. Cost scales with spans-per-
        # trace, not session lifetime — typical traces are <1k spans.
        _refresh_active_work_ms(conn, buckets.keys())
        # Only on the batch that ends the session. Re-merging the whole union
        # costs ~2x `_refresh_active_work_ms` and would run on EVERY batch from
        # BOTH writers, inside this exclusive write transaction — quadratic in
        # session length for exactly the long runs the SDK tier produces. The
        # served counts are group-aware on their own (`_shallow_map_response`),
        # so the stored counter only has to be right once the session is over.
        if any(span.get('name') == 'session.end' for span, _ in normalised):
            for _tid in buckets:
                _refresh_group_span_count(conn, _tid)
        _resolve_session_repos(conn, normalised, existing_set)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    inserted = len(normalised) - skipped
    _trace_log().write(
        'spans_ingested', inserted=inserted, skipped=skipped,
        trace_count=len(buckets),
    )
    return inserted, skipped
