"""Trace endpoints — split by URL grouping (skill-reads, mcp-calls, etc.)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone

from flask import request, jsonify, Response

from lib import hook_plugin as _hp
from lib.agent_sdk import resumable as resumable_mod
from lib.providers import canonical_agent_kind
from lib.trace.session_tags import (
    builtin_tag_for_origin, builtin_meta, is_builtin, normalize_custom_slug,
    origins_for_builtin, system_origins, BUILTIN_TAGS,
)
from lib.orm import SessionLocal
from lib.orm.models import (
    PlanSession, PromptImage, RuleTrigger, Session as SessionModel,
    SessionRepo, SessionSpan, SessionTag, SessionTraceMap, SkillRead, TurnUsage,
)
from lib.utils.pagination import clamp_size, keyset_page_stmt
from lib.trace import trace_service
from lib.trace.alias import (
    ORIGIN_KEY, aliased_run_ids, strip_origin, trace_group,
)


_VAGUE_END_REASONS = frozenset({None, '', 'other', 'unknown'})


def _group_ended_reason(group: list[str], stored):
    """Why the session ended, taking the concrete answer from either writer.

    The child's hook can only report the generic `other` for a run the SDK
    ended deliberately — only the runner knows it was an idle timeout. The span
    merge already prefers the concrete reason (`merge._fill_vague_attrs`), so
    without this the header and the session list contradict the `session.end`
    span in the very same response.

    Takes the caller's already-resolved group: a live session's reason is NULL,
    i.e. vague, so the early return below does not fire for it and re-resolving
    here would add a connection to every poll of every session.
    """
    if stored not in _VAGUE_END_REASONS or len(group) < 2:
        return stored
    from sqlmodel import select as _sel
    with SessionLocal() as session:
        for reason in session.exec(
                _sel(SessionModel.ended_reason)
                .where(SessionModel.trace_id.in_(group))).all():
            if reason not in _VAGUE_END_REASONS:
                return reason
    return stored


def _total_span_count(session, trace_id: str, loaded: int = 0) -> int:
    """Denominator for the "N of M loaded" indicator.

    Excludes PENDING placeholders (the append-only store keeps them).

    For an alias group the raw row count is the WRONG total: it sums both
    writers, so it double-counts every event they both recorded and would read
    "134 of 236" against a merged view. The merged count is what
    `sessions.span_count` holds for the canonical row, so use that — the same
    number the session list shows, which is also what makes the two agree.

    A subagent trace has no rows and no `sessions` row under its own id, so it
    is counted over the agent's scope instead — the raw count answers 0 there,
    and "N of 0 loaded" on a 569-span child is worse than no indicator at all.
    """
    from sqlalchemy import func as _fn
    from sqlmodel import select as _sel

    subagent_total = _subagent_span_count(trace_id)
    if subagent_total is not None:
        return subagent_total

    def raw_for(tid):
        return session.exec(
            _sel(_fn.count())
            .select_from(SessionSpan)
            .where(SessionSpan.trace_id == tid)
            .where(SessionSpan.status_code != 'PENDING')
        ).one()

    group = _alias_group_ids(trace_id)
    if len(group) < 2:
        return raw_for(trace_id)
    # Summing the group would double-count every event both writers recorded.
    # The exact answer is the merged count in `sessions.span_count`, but that
    # is only refreshed when the session ends — so for the whole life of a live
    # run it is one writer's stale figure and can fall BELOW what is on screen.
    # Merging per poll is far too slow, so take the best lower bounds we have:
    # the merge keeps every row of the larger writer plus the smaller writer's
    # unpaired ones, so it is never less than either raw count, and never less
    # than what the caller already loaded.
    stored = session.exec(
        _sel(SessionModel.span_count)
        .where(SessionModel.trace_id == group[0])
    ).first()
    return max(stored or 0, loaded, *(raw_for(t) for t in group))


def _expand_alias_groups(trace_ids: list[str]) -> list[str]:
    """Every trace id these sessions are actually made of, de-duplicated."""
    out: list[str] = []
    for trace_id in trace_ids:
        for member in _alias_group_ids(trace_id):
            if member not in out:
                out.append(member)
    return out


def _map_row_to_span(row, canonical: str) -> dict:
    """One `/map` row as a projection span dict.

    Re-keys to the canonical id and tags the writer, the same contract as
    `_fetch_spans_for_group` — without the tag `merge_spans` sees a single
    origin and reconciles nothing, leaving this view double-rendered where the
    other readers are not.
    """
    d = dict(row)
    try:
        d['attributes'] = json.loads(d['attributes']) if d.get('attributes') else {}
    except (TypeError, ValueError):
        d['attributes'] = {}
    d[ORIGIN_KEY] = 'hook' if d['trace_id'] == canonical else 'sdk'
    d['trace_id'] = canonical
    return d


def _alias_group_ids(trace_id: str) -> list[str]:
    """Every trace id whose spans belong to this session — itself for an
    ordinary one. Opens its own connection so the SQLModel-side readers can
    scope to the group without threading one through."""
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        return trace_group(conn, trace_id)
    finally:
        conn.close()


def _stored_group_ids(trace_id: str) -> list[str]:
    """The trace ids this session's rows are actually STORED under — the
    parent's group for a subagent trace, which owns no row of its own.

    What every session-level read has to key on: the `sessions` row behind the
    header, the workflow labels, and the read-side memo, whose entries a
    subagent trace would otherwise file under an id no writer ever
    invalidates — freezing a live child on its first answer."""
    from lib.orm.engine import get_connection
    from lib.trace.subagent_trace import stored_group
    conn = get_connection()
    try:
        return stored_group(conn, trace_id)
    finally:
        conn.close()


def _subagent_scope(trace_id: str):
    """`subagent_trace.AgentScope` for an `sa-…` id, else None.

    The prefix test comes first so the per-span content reads — one request per
    body the trace view opens — don't each pay for a connection to be told the
    id was ordinary."""
    from lib.orm.engine import get_connection
    from lib.trace.subagent_trace import agent_span_scope, is_subagent_trace_id
    if not is_subagent_trace_id(trace_id):
        return None
    conn = get_connection()
    try:
        return agent_span_scope(conn, trace_id)
    finally:
        conn.close()


def _subagent_span_count(trace_id: str) -> int | None:
    """The agent's own span total for an `sa-…` id, else None."""
    from lib.orm.engine import get_connection
    from lib.trace.subagent_trace import subagent_span_count
    conn = get_connection()
    try:
        return subagent_span_count(conn, trace_id)
    finally:
        conn.close()


def _scoped_span_query(stmt, trace_id: str, scope):
    """Narrow a span query to the rows `trace_id` serves.

    A subagent trace's rows are stored under its parent's id, so the group
    predicate alone would answer with the whole parent conversation; the agent
    scope is what keeps one child's read to one child.
    """
    if scope is None:
        return stmt.where(SessionSpan.trace_id.in_(_alias_group_ids(trace_id)))
    return (stmt.where(SessionSpan.trace_id.in_(scope.trace_ids))
                .where(SessionSpan.agent_id.in_(scope.agent_ids)))
from web.helpers import (
    _is_non_blank_str, _is_iso_timestamp, _normalize_is_test,
    _ingest_max_batch_size, _ingest_max_attributes_bytes,
)
# NOTE: `_INGEST_DEDUP_WINDOW_SEC` is looked up at *call* time via the
# module (not imported by value) so tests can monkeypatch
# `web.helpers._INGEST_DEDUP_WINDOW_SEC` and have the handler observe the
# new window. `from web import helpers as _helpers` preserves the live
# reference; doing `from web.helpers import _INGEST_DEDUP_WINDOW_SEC`
# would bind the initial value forever.
from web import helpers as _helpers
from lib.trace.pending_spans import (
    AGENT_ID_SQL,
    INACTIVE_THRESHOLD_SEC as _AGENT_STALE_AFTER_SEC,
    parse_naive_ts as _roster_ts,
)
from lib.trace.projection import (
    _fetch_spans, _graft_orphans, _widen_envelopes,
    _build_span_tree, _persist_projection,
)

from web.blueprints.trace import trace_bp


def _filter_sessions_by_repo(stmt, repo_name):
    """Narrow a sessions query to those tagged with `repo_name`.

    Matches by the unique registered repo name; a multi-repo session
    matches every repo it touched. No-op when `repo_name` is blank.
    """
    repo_name = (repo_name or '').strip()
    if not repo_name:
        return stmt
    from sqlmodel import select as _sel
    from lib.orm.models import Repo, SessionRepo
    return stmt.where(SessionModel.trace_id.in_(
        _sel(SessionRepo.trace_id)
        .join(Repo, Repo.id == SessionRepo.repo_id)
        .where(Repo.name == repo_name)
    ))


def _live_sdk_session_ids() -> set:
    """Session ids this process holds a live runner for, or empty.

    Best-effort like every other SDK read on this path: the tier is optional
    and gated off by default, and the session list must not depend on it
    being importable.
    """
    try:
        from lib import agent_sdk
        return agent_sdk.live_session_ids()
    except Exception:
        return set()


def _session_active_clause(want_active: bool):
    """The server's mirror of the frontend `sessionLiveness(row)` rule as SQL.

    'ended' is always inactive. Every other marker — 'active', unset, an
    unknown future value — is subject to the last-seen recency check: a
    session whose SessionEnd hook never fired (crash, SIGKILL, a worktree
    deleted under it) keeps status='active' forever, and nothing on the
    server corrects it. One definition so the `active=` filter, the
    `active_count` facet, and the green badge can never drift apart; the
    frontend half lives in frontend/src/utils/sessionStatus.js
    (`isActiveRowStatus`).

    Recency is the fallback, not the rule. For a run regin LAUNCHED the
    registry knows: an agent parked at the prompt emits no spans, so recency
    alone ages it out while the runner is still holding it. Those ids are
    live regardless of `last_seen` — but still subject to `not_ended`, since
    `_aliases` outlives a child that `/clear` retired.
    """
    from sqlalchemy import or_ as _or, and_ as _and, not_ as _not
    cutoff_iso = (datetime.now() - timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M:%S')
    status_col = SessionModel.status
    not_ended = _or(status_col.is_(None), status_col != 'ended')
    alive = SessionModel.last_seen >= cutoff_iso
    live_ids = _live_sdk_session_ids()
    if live_ids:
        alive = _or(alive, SessionModel.trace_id.in_(live_ids))
    if want_active:
        return _and(not_ended, alive)
    return _or(status_col == 'ended', _and(not_ended, _not(alive)))


def _repo_counts(session, narrow):
    """Sessions-per-registered-repo over the caller's full filter set.

    A multi-repo session counts once under every repo it touched, matching
    what `repo=` would return for each — so the facet chip's number is the
    result count you get by clicking it.
    """
    from sqlalchemy import func as _func
    from sqlmodel import select as _select
    from lib.orm.models import Repo
    rows = session.exec(
        narrow(_select(Repo.name, _func.count(_func.distinct(SessionRepo.trace_id)))
               .select_from(SessionModel)
               .join(SessionRepo, SessionRepo.trace_id == SessionModel.trace_id)
               .join(Repo, Repo.id == SessionRepo.repo_id))
        .group_by(Repo.name)
    ).all()
    return {name: n for name, n in rows}


def _list_facet_counts(session, narrow):
    """`total_count` / `active_count` for the list envelope.

    `narrow` applies the request's COMPLETE filter set (shared WHERE + origin
    + tag), so both numbers describe the same population the page paginates
    over — the footer total and the header "N active now" pill stay consistent
    with the rows on screen.
    """
    from sqlalchemy import func as _func
    from sqlmodel import select as _select

    def _count(*where):
        return session.exec(
            narrow(_select(_func.count()).select_from(SessionModel)).where(*where)
        ).one()

    return {
        'total_count': _count(),
        'active_count': _count(_session_active_clause(True)),
    }


def _attach_session_repos(session, items) -> None:
    """Attach `repos`, `is_multi_repo`, `primary_repo` to each row dict.

    One batched query for the whole page (no N+1). `repos` lists every
    registered repo the session touched, primary first.
    """
    if not items:
        return
    from sqlmodel import select as _sel
    from lib.orm.models import Repo, SessionRepo

    trace_ids = [it.get('trace_id') for it in items if it.get('trace_id')]
    by_tid: dict = {}
    if trace_ids:
        rows = session.exec(
            _sel(SessionRepo.trace_id, SessionRepo.repo_id,
                 SessionRepo.is_primary, Repo.name)
            .join(Repo, Repo.id == SessionRepo.repo_id)
            .where(SessionRepo.trace_id.in_(trace_ids))
        ).all()
        for tid, _rid, is_primary, name in rows:
            by_tid.setdefault(tid, []).append(
                {'name': name, 'is_primary': bool(is_primary)})
    for it in items:
        repos = by_tid.get(it.get('trace_id'), [])
        repos.sort(key=lambda r: (not r['is_primary'], r['name']))
        it['repos'] = repos
        it['is_multi_repo'] = len(repos) > 1
        it['primary_repo'] = next(
            (r['name'] for r in repos if r['is_primary']), None)


# ── Sessions list + detail + materialize ───────────────────────

# Origins the `workflow=hide|only` toggle treats as non-interactive runs:
# captured dynamic-workflow runs and regin-spawned LLM stages. NULL / any
# other origin reads as an interactive session. Derived from the `system`
# builtin tag so the legacy toggle and the tag facet share one definition
# of "a run" (see lib/trace/session_tags.py).
_RUN_ORIGINS = system_origins()

# Every origin explicitly owned by a non-fallback builtin category — used to
# express the fallback `user` tag as "origin not claimed by another builtin".
_CLAIMED_ORIGINS = tuple(o for t in BUILTIN_TAGS for o in t["origins"])


# ── Session tags: derived-builtin + stored-custom, one flat surface ──

# Sentinel slug: a `tag=` was requested but nothing valid survived. Resolves
# to an always-false clause so a garbage slug narrows to empty rather than
# silently widening to the unfiltered list.
_IMPOSSIBLE_TAG = '\x00none'


def _parse_tag_filter():
    """Validated tag slugs from the repeatable `tag=` query param (AND-ed).

    Builtin slugs pass through; custom slugs are normalized. An empty `tag=`
    means "no selection". A non-empty but unusable slug (bad charset) yields
    the impossible sentinel so the result narrows to nothing rather than
    500ing or silently returning everything.
    """
    slugs = []
    requested = False
    for raw in request.args.getlist('tag'):
        bare = raw.strip().lower() if isinstance(raw, str) else ''
        if bare:
            requested = True
        candidate = bare if is_builtin(bare) else normalize_custom_slug(raw)
        if candidate and candidate not in slugs:
            slugs.append(candidate)
    if requested and not slugs:
        return [_IMPOSSIBLE_TAG]
    return slugs


def _tag_where(slug):
    """A WHERE clause selecting sessions carrying `slug`.

    A builtin category slug resolves to an `origin` predicate (single axis);
    the fallback `user` tag is "origin owned by no other builtin". A custom
    slug resolves to membership in the `session_tags` join table.
    """
    from sqlalchemy import or_ as _or, false as _false
    from sqlmodel import select as _select
    from lib.orm.models.trace import SessionTag as _SessionTag
    if slug == _IMPOSSIBLE_TAG:
        return _false()
    if is_builtin(slug):
        origins = origins_for_builtin(slug)
        if origins is None:
            return _or(SessionModel.origin.is_(None),
                       SessionModel.origin.not_in(_CLAIMED_ORIGINS))
        return SessionModel.origin.in_(origins)
    return SessionModel.trace_id.in_(
        _select(_SessionTag.trace_id).where(_SessionTag.tag == slug))


def _apply_tag_filter(stmt, slugs):
    """AND every selected tag onto `stmt` (empty list → unchanged)."""
    for slug in slugs:
        stmt = stmt.where(_tag_where(slug))
    return stmt


def _attach_session_tags(session, items):
    """Give each row a flat `tags` list: derived builtin category first, then
    stored custom tags. One query over the page's trace_ids (mirrors
    `_attach_session_repos`)."""
    from sqlmodel import select as _select
    from lib.orm.models.trace import SessionTag as _SessionTag
    trace_ids = [it.get('trace_id') for it in items if it.get('trace_id')]
    custom = {}
    if trace_ids:
        rows = session.exec(
            _select(_SessionTag.trace_id, _SessionTag.tag, _SessionTag.source)
            .where(_SessionTag.trace_id.in_(trace_ids))
        ).all()
        for tid, tag, source in rows:
            custom.setdefault(tid, []).append((tag, source or 'manual'))
    for it in items:
        builtin = builtin_tag_for_origin(it.get('origin'))
        tags = [{'slug': builtin, 'source': 'auto', 'builtin': True}]
        for tag, source in sorted(custom.get(it.get('trace_id'), [])):
            tags.append({'slug': tag, 'source': source, 'builtin': False})
        it['tags'] = tags


def _tag_counts(session, apply_filters):
    """Per-tag counts over the same filter set (minus the tag filter itself),
    for the facet. Builtin counts fold a `GROUP BY origin`; custom counts join
    `session_tags`. `apply_filters` is the caller's shared-WHERE closure."""
    from sqlalchemy import func as _func
    from sqlmodel import select as _select
    from lib.orm.models.trace import SessionTag as _SessionTag
    counts = {}
    origin_rows = session.exec(
        apply_filters(_select(SessionModel.origin, _func.count())
                      .select_from(SessionModel))
        .group_by(SessionModel.origin)
    ).all()
    for origin, n in origin_rows:
        slug = builtin_tag_for_origin(origin)
        counts[slug] = counts.get(slug, 0) + n
    custom_rows = session.exec(
        apply_filters(_select(_SessionTag.tag, _func.count())
                      .select_from(SessionModel)
                      .join(_SessionTag,
                            _SessionTag.trace_id == SessionModel.trace_id))
        .group_by(_SessionTag.tag)
    ).all()
    for tag, n in custom_rows:
        counts[tag] = n
    return counts


@trace_bp.route('/api/sessions')
def api_sessions():
    """Keyset-paginated session list, newest first.

    Cursor is (last_seen DESC, trace_id DESC) — trace_id is the PK and
    is unique, so identical last_seen timestamps never collide.

    Query params:
      include_tests=true — legacy alias for `kind=all` (keeps existing tests
                           and external callers working). Overridden when
                           `kind` is set explicitly.
      kind=real|test|all — which session population to return:
                           real → only is_test=0 (default, matches legacy)
                           test → only is_test=1
                           all  → both
      active=active|inactive|all
                         — match the same rule the table uses for the
                           green "active" badge: status='active' OR
                           (status unset AND last_seen within 10 minutes).
      trace_id=<prefix>  — case-insensitive prefix match on trace_id.
      q=<text>           — case-insensitive substring search term
      scope=title|prompt|both — where `q` matches:
                           title  → SessionModel.title (default; legacy)
                           prompt → any `prompt` span's `text` attribute
                           both   → OR of the two
      since=<iso>        — inclusive lower bound on last_seen (naive local ISO,
                           lex-compared against stored text)
      until=<iso>        — exclusive upper bound on last_seen
      repo=<name>        — only sessions tagged with this registered repo
                           (unique repo name). Multi-repo sessions match
                           every repo they touched.
      workflow=hide|show|only
                         — filter by the `origin` axis. show (server default) →
                           every row, so external callers and E2E fixtures are
                           unaffected; hide → exclude non-interactive runs
                           (origin in `_RUN_ORIGINS`: captured workflow runs
                           and regin-spawned LLM stages); only → just those.
                           SessionsView sends 'hide' by default to keep runs out
                           of the main list. When 'hide' the envelope carries
                           `workflow_hidden_count` — the number of run-origin
                           rows the same other filters would have matched —
                           for a pivot hint.
      cursor, size       — keyset pagination (see lib.utils.pagination)
    """
    from sqlmodel import select as _select
    from sqlalchemy import func as _func, or_ as _or
    include_tests = request.args.get('include_tests', 'false').lower() in ('1', 'true', 'yes')
    kind = (request.args.get('kind') or '').strip().lower()
    if kind not in ('real', 'test', 'all'):
        # Fall back to legacy `include_tests`: present → 'all', absent → 'real'.
        kind = 'all' if include_tests else 'real'
    active_filter = (request.args.get('active') or 'all').strip().lower()
    if active_filter not in ('all', 'active', 'inactive'):
        active_filter = 'all'
    trace_id_q = (request.args.get('trace_id') or '').strip()
    search = (request.args.get('q') or '').strip()
    scope = (request.args.get('scope') or 'title').strip().lower()
    if scope not in ('title', 'prompt', 'both'):
        scope = 'title'
    since = (request.args.get('since') or '').strip()
    until = (request.args.get('until') or '').strip()
    date_filtered = bool(since or until)
    workflow = (request.args.get('workflow') or 'show').strip().lower()
    if workflow not in ('hide', 'show', 'only'):
        workflow = 'show'
    tag_slugs = _parse_tag_filter()
    cursor_token = request.args.get('cursor')
    size = clamp_size(request.args.get('size'), default=50)

    from lib.tokens.model_windows import infer_window as _infer_window

    def _row_to_dict(row) -> dict:
        # Normalized to naive local (`parse_naive_ts`), never raw awareness:
        # a row can mix a tz-aware `started_at` with a naive `last_seen`
        # (writers disagree — spec-posted spans stamp `…Z`, hooks stamp
        # local), and one such row's subtraction 500s the whole listing.
        from lib.trace.pending_spans import parse_naive_ts as _parse_iso

        d = dict(row._mapping)
        # The runner's own verdict, for a run regin LAUNCHED — None for every
        # session it merely traces, which is what makes its presence the
        # signal the client switches on. Cheap enough to do per row: it is a
        # dict lookup under a lock (`agent_sdk.run_phase`), no query and no
        # spans, so it does not put this list on the roster's phasing cost.
        #
        # It is here because recency cannot answer the question a launched run
        # can: an agent parked at the prompt emits no spans, so after the stale
        # window the list called it `inactive` while the switcher — which does
        # ask the runner — called it alive. Same session, two verdicts.
        d['runner_phase'] = _owned_run_phase(d.get('trace_id'))
        # Derive context_pct using the session's richer `model` id (which
        # carries the [1m] variant suffix when applicable) rather than
        # the per-turn-span `context_window_tokens` — the handler
        # computes that from the transcript's bare `message.model` and
        # therefore can't see the 1M promotion. `context_pct` is the
        # main-conversation peak (terminal-matching, used as headline);
        # `context_pct_all` is the all-inclusive peak (rolled-in
        # advisor / server-side sub-call tokens) — UI shows it as a
        # secondary chip only when the two diverge. The headline divides
        # the *live* peak (main-flow high-water mark since the last
        # `/compact`) so the % drops when the session compacts; the
        # all-time peaks still drive window inference and the
        # pre-compaction hint.
        peak_full = d.get('peak_context_tokens')
        peak_main = d.get('peak_main_context_tokens')
        live = d.get('live_context_tokens')
        model = d.get('model')
        if isinstance(peak_full, int):
            win = _infer_window(model, peak_full)
            d['context_window_tokens'] = win  # override stored hint
            d['context_pct_all'] = round(peak_full * 100.0 / win, 1) if win > 0 else None
            main_for_pct = next(
                (v for v in (live, peak_main, peak_full) if isinstance(v, int)),
                peak_full)
            d['context_pct'] = round(main_for_pct * 100.0 / win, 1) if win > 0 else None
        else:
            d['context_pct'] = None
            d['context_pct_all'] = None

        # Pre-compute active_pct and idle_ms so the frontend doesn't need
        # to parse timestamps and do arithmetic per rendered row.
        active_ms = d.get('active_work_ms')
        t0 = _parse_iso(d.get('started_at'))
        t1 = _parse_iso(d.get('last_seen'))
        if active_ms is not None and t0 and t1:
            total_ms = int((t1 - t0).total_seconds() * 1000)
            if total_ms > 0:
                d['active_pct'] = round(active_ms * 100.0 / total_ms)
                d['idle_ms'] = max(0, total_ms - active_ms)
            else:
                d['active_pct'] = None
                d['idle_ms'] = 0
        else:
            d['active_pct'] = None
            d['idle_ms'] = None

        # Canonical agent kind: 'claude' | 'codex' | 'kimi' | 'generic' | None.
        # Delegated to the provider registry so the vendor→kind mapping lives
        # in one place (lib/providers/registry.canonical_agent_kind) rather
        # than a substring chain copied into this blueprint. agent_type is
        # vendor-only here — "workflow" moved to the orthogonal `origin` axis
        # below. Keep raw agent_type for display (tooltip uses it verbatim).
        d['agent_kind'] = canonical_agent_kind(d.get('agent_type'))

        # `origin` is what KIND of row this is: 'session' (a real interactive
        # agent session, the default), 'workflow' (a captured dynamic-
        # workflow run), or 'llm-stage' (a regin-spawned LLM stage). NULL
        # legacy rows read as 'session'.
        d['origin'] = d.get('origin') or 'session'
        # Builtin category tag (user / topic-proposal / system), derived from
        # the single `origin` axis. The full `tags` list (this + any stored
        # custom tags) is attached in bulk by `_attach_session_tags`.
        d['category'] = builtin_tag_for_origin(d['origin'])
        d['is_workflow'] = (d['origin'] == 'workflow')
        # Generalised run-ness: True for every origin the workflow=hide
        # toggle filters, so the frontend never re-derives the origin list.
        d['is_run'] = d['origin'] in _RUN_ORIGINS

        return d

    from lib.orm.models.trace import PlanSession as _PlanSession
    # `plans` is computed live from the plan_sessions table rather than a
    # stored counter: it counts the distinct plan files this session
    # authored/edited, so it stays correct as new plans land without an
    # ingest-time counter (the old `plan_enters` span counter is dead —
    # `plan.enter` spans are no longer emitted). Tiny table; the
    # correlated subquery is cheap.
    plans_subq = (
        _select(_func.count(_func.distinct(_PlanSession.plan_filename)))
        .where(_PlanSession.session_id == SessionModel.trace_id)
        .correlate(SessionModel)
        .scalar_subquery()
        .label('plans')
    )

    def _apply_origin(s):
        """The `workflow` (origin) axis, applied AFTER the shared filters.

        NULL legacy rows count as 'session' (not a run), so `hide` keeps them
        and `only` drops them. 'llm-stage' rows (regin-spawned LLM stages,
        e.g. reflect's judges) ride the same toggle as workflow captures —
        both are non-interactive runs.
        """
        if workflow == 'only':
            return s.where(SessionModel.origin.in_(_RUN_ORIGINS))
        if workflow == 'hide':
            return s.where(_or(SessionModel.origin.is_(None),
                               SessionModel.origin.not_in(_RUN_ORIGINS)))
        return s

    def _apply_filters(s, skip_date=False, skip_repo=False):
        """Apply the shared WHERE set (kind/is_test, trace_id, active,
        title/prompt search, since/until, repo) to a statement and return it.

        Extracted so the page query and the workflow_hidden_count query run
        an identical filter set — the count must reflect exactly the rows the
        page would have shown but for the `workflow` origin filter. The
        workflow/origin clause is applied by the caller, NOT here.
        `skip_date=True` omits the since/until bounds so callers can count
        rows outside the current date window; `skip_repo=True` omits the repo
        narrowing so a facet can still show its sibling repos' counts.
        """
        if kind == 'real':
            s = s.where(SessionModel.is_test == 0)
        elif kind == 'test':
            s = s.where(SessionModel.is_test == 1)
        if trace_id_q:
            s = s.where(SessionModel.trace_id.ilike(f"{trace_id_q}%"))
        elif hidden_alias_ids:
            # A regin-launched run is traced twice — once by the runner, once
            # by the child `claude`'s own hooks — and the reader serves both
            # ids as one merged session. Listing both would show the same run
            # twice. The alias is hidden, never the canonical row, and only
            # when the caller is browsing: an explicit `trace_id` prefix is a
            # deep link and must still resolve the run's own id, which is what
            # `/live` links carry.
            s = s.where(SessionModel.trace_id.notin_(hidden_alias_ids))
        if active_filter != 'all':
            s = s.where(_session_active_clause(active_filter == 'active'))
        if search:
            # COLLATE NOCASE via .ilike on SQLite; for MySQL the default
            # collation already matches case-insensitively on varchar columns.
            title_clause = SessionModel.title.ilike(f"%{search}%")
            # Substring match on the `text` attribute of `prompt` spans.
            # json_extract isolates the field so a hit on (say) a tool
            # input that happens to contain the term doesn't pollute the
            # result set. SQLite 3.38+ ships json1 by default.
            from lib.orm.models.trace import SessionSpan
            prompt_clause = SessionModel.trace_id.in_(
                _select(SessionSpan.trace_id)
                .where(SessionSpan.name == 'prompt')
                .where(
                    _func.json_extract(SessionSpan.attributes, '$.text')
                    .ilike(f"%{search}%")
                )
            )
            search_clause = {'title': title_clause, 'prompt': prompt_clause}.get(
                scope, _or(title_clause, prompt_clause))
            s = s.where(search_clause)
        if not skip_date:
            if since:
                s = s.where(SessionModel.last_seen >= since)
            if until:
                s = s.where(SessionModel.last_seen < until)
        if skip_repo:
            return s
        return _filter_sessions_by_repo(s, request.args.get('repo'))

    def _narrow(s, skip_repo=False):
        """The request's COMPLETE filter set: shared WHERE + origin + tag."""
        return _apply_tag_filter(_apply_origin(_apply_filters(s, skip_repo=skip_repo)),
                                 tag_slugs)

    with SessionLocal() as session:
        hidden_alias_ids = aliased_run_ids(session)
        stmt = _apply_filters(_select(
            SessionModel.trace_id, SessionModel.title, SessionModel.title_source,
            SessionModel.status, SessionModel.ended_at, SessionModel.ended_reason,
            SessionModel.started_at, SessionModel.last_seen,
            SessionModel.span_count, SessionModel.skill_reads,
            SessionModel.file_edits, SessionModel.rule_checks,
            plans_subq, SessionModel.prompts,
            SessionModel.tool_calls, SessionModel.is_test, SessionModel.test_name,
            SessionModel.agent_type,
            SessionModel.origin,
            SessionModel.model,
            SessionModel.cwd,
            SessionModel.input_tokens, SessionModel.output_tokens,
            SessionModel.cache_read_tokens, SessionModel.cache_creation_tokens,
            SessionModel.peak_context_tokens, SessionModel.peak_main_context_tokens,
            SessionModel.live_context_tokens,
            SessionModel.context_window_tokens,
            SessionModel.active_work_ms,
            SessionModel.cost_usd,
        ))
        stmt = _narrow(stmt)

        # Per-tag counts for the facet, over the shared filters but NOT the
        # tag selection itself (so a selected facet still shows sibling counts).
        tag_counts = _tag_counts(session, _apply_filters)

        # Envelope facets: total/active over the SAME population the page
        # paginates, and repo counts over everything but the repo selection
        # (same sibling-counts rule as tag_counts).
        facets = _list_facet_counts(session, _narrow)
        facets['repo_counts'] = _repo_counts(
            session, lambda s: _narrow(s, skip_repo=True))

        # When hiding runs, count how many the SAME other filters would have
        # matched so the frontend can offer a pivot hint. Same _apply_filters,
        # restricted to the run origins.
        if workflow == 'hide':
            hidden_workflow_count = session.exec(
                _apply_filters(_select(_func.count()).select_from(SessionModel))
                .where(SessionModel.origin.in_(_RUN_ORIGINS))
            ).one()
        else:
            hidden_workflow_count = None

        # When showing only runs and a date filter is active, count how many
        # workflow rows the date filter excluded, for a "widen date range" hint.
        if workflow == 'only' and date_filtered:
            wf_all = session.exec(
                _apply_filters(_select(_func.count()).select_from(SessionModel), skip_date=True)
                .where(SessionModel.origin.in_(_RUN_ORIGINS))
            ).one()
            wf_dated = session.exec(
                _apply_filters(_select(_func.count()).select_from(SessionModel))
                .where(SessionModel.origin.in_(_RUN_ORIGINS))
            ).one()
            workflow_date_hidden_count = max(0, wf_all - wf_dated)
        else:
            workflow_date_hidden_count = None

        page = keyset_page_stmt(
            session, stmt,
            order_cols=[(SessionModel.last_seen, 'DESC'),
                        (SessionModel.trace_id, 'DESC')],
            cursor_token=cursor_token, size=size,
            row_to_dict=_row_to_dict,
        )
        _attach_session_repos(session, page.items)
        _attach_session_tags(session, page.items)
    envelope = page.to_envelope()
    envelope['sessions'] = envelope['items']  # legacy field
    envelope['search'] = search
    envelope['workflow_hidden_count'] = hidden_workflow_count
    envelope['workflow_date_hidden_count'] = workflow_date_hidden_count
    envelope['tag_counts'] = tag_counts
    envelope['builtin_tags'] = builtin_meta()
    envelope.update(facets)
    # One server-clock instant in BOTH stored-timestamp formats (naive
    # host-local and UTC-Z), so the frontend can age each row server−server
    # against the anchor matching that row's format. Client−server arithmetic
    # leaks the host↔viewer timezone offset: a viewer behind the host reads
    # every recent naive stamp as future ("just now").
    now = datetime.now(timezone.utc)
    envelope['server_now'] = (
        now.astimezone().replace(tzinfo=None).isoformat(timespec='microseconds'))
    envelope['server_now_utc'] = now.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    return jsonify(envelope)


def _wire_nodes(nodes, *, deep):
    """Project tree nodes for the wire: structure only, spans named by id.

    Every response that carries a tree also carries the span bodies in its
    `spans` list, so a node embedding its own `data` copy shipped each span
    twice — and a group node, whose `data.spans` inlines its children's
    bodies, shipped some of them a third time. One prompt's `?deep=1` subtree
    came to 841KB, of which 491KB was the second and third copies. The client
    rejoins the two halves on arrival (`hydrateNodes` in useTraceData).

    What survives is what belongs to the NODE rather than to its span: the
    expander flags, the child count, and a group's rolled-up envelope, which
    spans the whole subtree and so differs from the group span's own times.

    `deep=False` is the lazy-expand shape — direct children only, each marked
    `leaf` so the frontend knows whether to offer an expander.
    """
    out = []
    for node in nodes:
        data = node['data']
        children = node.get('children') or []
        wire = {'key': node['key'], 'span_id': data['span_id']}
        if data.get('isGroup'):
            wire['isGroup'] = True
            wire['start_time'] = data.get('start_time')
            wire['end_time'] = data.get('end_time')
        if data.get('isSpan'):
            wire['isSpan'] = True
        if deep:
            if children:
                wire['children'] = _wire_nodes(children, deep=True)
        else:
            wire['leaf'] = not children
            if children:
                inline = data.get('spans')
                wire['child_count'] = len(inline) if inline else len(children)
        out.append(wire)
    return out


def _shallow_nodes(nodes):
    """Direct-children wire nodes (no nesting) — see `_wire_nodes`."""
    return _wire_nodes(nodes, deep=False)


@trace_bp.route('/api/sessions/<trace_id>')
def api_session_detail(trace_id):
    """Read-only: projects spans into a tree in memory. Does not mutate the DB.

    Orphan-grafting and parent-envelope widening happen as a pure projection
    on every call. Callers that want the cleanup persisted (e.g. for analytics
    queries) should POST to /api/sessions/<trace_id>/materialize.

    Pass `?shallow=1` to return only the first-class (root) spans; children
    are fetched on demand via /api/sessions/<trace_id>/spans/<span_id>/children.
    """
    shallow = request.args.get('shallow', '').lower() in ('1', 'true')
    widened, tree = trace_service.fetch_session_projection(
        trace_id, drop_delegated=True)
    summary = _session_summary(trace_id)
    if shallow:
        root_ids = {n['data']['span_id'] for n in tree}
        return jsonify({
            'trace_id': trace_id,
            'spans': [s for s in widened if s['span_id'] in root_ids],
            'tree': _shallow_nodes(tree),
            'span_count_total': len(widened),
            **summary,
        })
    return jsonify({
        'trace_id': trace_id,
        'spans': widened,
        'tree': tree,
        'span_count_total': len(widened),
        **summary,
    })


# TaskCreate/TaskUpdate carry ONE task per span; the snapshot tool — Kimi's
# `TodoList` — resends the WHOLE list on every write. Both feed the same event
# log. Claude Code's `TodoWrite` is deliberately excluded: it is not recorded as
# a snapshot span at all (hook_manager/handlers/post_tool_trace.py).
_TASK_SPAN_NAMES = ('tool.TaskCreate', 'tool.TaskUpdate', 'tool.TodoList')

# Statuses a dropped task is never rewritten to `deleted` from — only an
# already-retired one, so a task retires on the snapshot that dropped it and
# not on every snapshot after.
_UNRETIRABLE_STATUSES = frozenset({'deleted'})


def _fetch_session_task_list(trace_id: str) -> dict | None:
    """Return the session's task-list events plus its final snapshot.

    TodoWrite-style task lists evolve over the life of the session —
    subject lives on the early TaskCreate, status flips via TaskUpdate
    spans scattered across later prompts. Two consumers need different
    views of that:

    * The conversation spine wants ONE card per task-write span,
      snapshotting the list as the model saw it AT THAT POINT (no
      future state). Replaying events client-side keeps the payload
      lean instead of shipping 53 near-identical snapshot blobs.
    * The session-header badge wants the FINAL state across the
      whole session.

    Computing both here (not client-side) lets the frontend render the
    full list even when most prompts are unexpanded — the conversation
    view lazy-loads each prompt's children, so a client-side scan over
    `props.spans` would drop tasks whose owning prompt isn't open.

    Returns `{events: [...], final: [...]}` or `None` if the session
    never used Task tools. Each event is `{span_id, timestamp, task_id,
    subject?, status?, order?, agent?}`; absent fields mean "this span didn't
    touch that field" (`order` is a snapshot span's payload position, `agent`
    the writing agent — absent for the main one). `final` is the LIVE snapshot
    after the last event (retired tasks dropped), grouped by owning agent and
    ordered the way that agent last wrote its list (snapshot position, falling
    back to numeric task_id).
    """
    from sqlmodel import select as _select
    with SessionLocal() as session:
        rows = session.exec(
            _select(
                SessionSpan.span_id, SessionSpan.name,
                SessionSpan.start_time, SessionSpan.attributes,
                SessionSpan.agent_id,
            )
            .where(SessionSpan.trace_id == trace_id)
            .where(SessionSpan.name.in_(_TASK_SPAN_NAMES))
            .order_by(SessionSpan.start_time.asc(), SessionSpan.id.asc())
        ).all()
    if not rows:
        return None
    events: list[dict] = []
    state: dict[str, dict] = {}
    # Per-task, track the most recent span_id that set each status.
    # After the loop the final entry's `current_span_id` resolves to
    # the latest span that produced its final status — the header
    # click-to-jump lands on "where the work for this task ended up"
    # rather than always on its TaskCreate. Pending tasks (never
    # updated) fall back to the TaskCreate span.
    last_span_by_status: dict[str, dict[str, str]] = {}
    # (agent_id, subject) → task_id, for the snapshot tool whose todos carry no
    # id. Keyed by agent because a subagent keeps its OWN list in the same
    # trace: sharing identity across agents would let one agent's snapshot
    # retire another's live tasks.
    todo_ids: dict[tuple[str, str], str] = {}
    for r in rows:
        _apply_task_row(r, events, state, last_span_by_status, todo_ids)
    if not events:
        return None
    final = _finalize_task_state(state, last_span_by_status)
    return {'events': events, 'final': final}


def _str_attr(value) -> str:
    """Coerce an attribute to a non-empty string, or '' otherwise."""
    return value if isinstance(value, str) and value else ''


def _apply_task_row(r, events, state, last_span_by_status, todo_ids=None) -> None:
    """Fold one task-write span into the event log + state.

    A snapshot span (`todos` attr) expands into one event per task; a
    TaskCreate/TaskUpdate span contributes its single task.
    """
    try:
        attrs = json.loads(r.attributes) if r.attributes else {}
    except (ValueError, TypeError):
        attrs = {}
    # The column is promoted from attributes at insert; rows written before
    # that promotion only carry the attribute.
    agent = _str_attr(getattr(r, 'agent_id', None)) or _str_attr(
        attrs.get('agent_id'))
    todos = attrs.get('todos')
    if isinstance(todos, list) and todos:
        _apply_todo_snapshot(r, todos, agent, events, state,
                             last_span_by_status,
                             todo_ids if todo_ids is not None else {})
        return
    tid = attrs.get('task_id')
    if tid is None:
        return
    _record_task_event(r, {
        'task_id': str(tid),
        'subject': _str_attr(attrs.get('subject')),
        'status': _str_attr(attrs.get('status')),
        'active_form': _str_attr(attrs.get('active_form')),
    }, events, state, last_span_by_status, agent)


def _record_task_event(r, task: dict, events, state, last_span_by_status,
                       agent: str = '') -> None:
    """Append one task's event (omitting subject/status when absent) and update
    the running per-task entry: first non-empty subject wins, every set status
    both overwrites the entry and records the span_id under that status in
    `last_span_by_status`. A snapshot-supplied `order` overwrites — it is the
    task's position in the LATEST whole-list payload, so the rendered list
    tracks the order the agent last wrote, not first-seen order. `agent` rides
    along so both surfaces can keep one agent's list whole instead of
    interleaving two agents' payload positions.
    """
    tid, status = task['task_id'], task['status']
    optional = {
        'subject': task['subject'],
        'status': status,
        'order': task.get('order'),
        'agent': agent,
    }
    events.append({
        'span_id': r.span_id,
        'timestamp': r.start_time,
        'task_id': tid,
        **{k: v for k, v in optional.items() if v is not None and v != ''},
    })
    entry = state.setdefault(tid, {
        'task_id': tid,
        'subject': '',
        'status': 'pending',
        'active_form': '',
        'agent_id': agent,
        # First TaskCreate's span_id — fallback for pending tasks.
        'created_span_id': r.span_id,
    })
    for field in ('subject', 'active_form'):
        if task[field] and not entry[field]:
            entry[field] = task[field]
    if task.get('order') is not None:
        entry['order'] = task['order']
    if status:
        entry['status'] = status
        last_span_by_status.setdefault(tid, {})[status] = r.span_id


def _apply_todo_snapshot(r, todos, agent, events, state, last_span_by_status,
                         todo_ids) -> None:
    """Fold one whole-list task write (`tool.TodoList`) by its writing agent.

    Its todos carry no id, and a session can replace the list wholesale (a new
    plan reuses position 1 for a different task), so identity is the subject:
    `todo_ids` hands each newly-seen (agent, subject) the next ordinal.
    Position-keyed ids would be one span shorter but graft a replacement's
    subject onto the old task's history. Display order comes from the event's
    `order` field (position in this payload), not the minted id, so a
    reordered list renders the way the agent last wrote it.
    """
    seen: set[str] = set()
    for position, todo in enumerate(todos):
        if not isinstance(todo, dict):
            continue
        subject = _str_attr(todo.get('subject'))
        if not subject:
            continue
        # Namespaced so a minted snapshot id can never collide with a
        # provider-supplied TaskCreate/TaskUpdate `task_id`, which is a bare
        # ordinal drawn from the same `state` keyspace.
        tid = todo_ids.setdefault((agent, subject), f'todo-{len(todo_ids) + 1}')
        seen.add(tid)
        _record_task_event(r, {
            'task_id': tid,
            'subject': subject,
            'status': _str_attr(todo.get('status')) or 'pending',
            'active_form': _str_attr(todo.get('active_form')),
            'order': position,
        }, events, state, last_span_by_status, agent)
    _retire_missing_todos(r, agent, seen, events, state, last_span_by_status,
                          todo_ids)


def _retire_missing_todos(r, agent, seen, events, state, last_span_by_status,
                          todo_ids) -> None:
    """A task the newest snapshot dropped is recorded `deleted` — that's how the
    task-list surfaces retire it, so the card keeps matching the list the model
    now sees instead of stranding the old plan's items as open work.

    Finished tasks retire too: a snapshot payload IS the whole list, so a plan
    the agent has moved on from is gone from its terminal regardless of how its
    items ended. Sparing `completed` instead accumulated every plan a long
    session ever wrote into one list, and their payload positions collided
    (CAI-44). Nothing is lost — `events` still carries each write, so the card
    for an earlier span replays that plan intact.

    Only the writing agent's OWN tasks are eligible: a subagent's list is a
    different list that happens to share the trace, and retiring across agents
    would mark the main agent's live work deleted (and vice versa).
    """
    for (owner, _subject), tid in todo_ids.items():
        if owner != agent or tid in seen:
            continue
        if state.get(tid, {}).get('status') in _UNRETIRABLE_STATUSES:
            continue
        _record_task_event(r, {
            'task_id': tid, 'subject': '', 'status': 'deleted',
            'active_form': '',
        }, events, state, last_span_by_status, agent)


def _finalize_task_state(state, last_span_by_status) -> list[dict]:
    """Resolve `current_span_id` per task and return the sorted live snapshot.

    Retired tasks are dropped: `final` is what the session's agents have on
    their lists NOW, and every consumer either filtered `deleted` itself or
    (the /live card) miscounted it as open work.

    `current_span_id` is the latest span that set the FINAL status,
    falling back to the TaskCreate for pending tasks. Tasks group by owning
    agent — main agent first, then each subagent in the order its first STILL-
    LIVE task was created — because two agents' payload positions both start
    at 0 and would otherwise interleave
    into one unreadable list. Within an agent, tasks a snapshot span
    positioned (an `order` from the latest whole-list payload) sort by that
    position — the order the agent last wrote; unpositioned tasks
    (TaskCreate/TaskUpdate numeric ids) keep their legacy order: numeric ids
    numerically, non-digit ids sunk to the end lexically.
    """
    live = [e for e in state.values() if e['status'] != 'deleted']
    for entry in live:
        per_status = last_span_by_status.get(entry['task_id'], {})
        entry['current_span_id'] = (
            per_status.get(entry['status']) or entry['created_span_id']
        )
    ranks: dict[str, int] = {'': 0}
    for entry in live:
        ranks.setdefault(entry.get('agent_id') or '', len(ranks))
    return sorted(live, key=lambda t: _task_sort_key(t, ranks))


def _task_sort_key(t: dict, ranks: dict[str, int]) -> tuple:
    agent = ranks.get(t.get('agent_id') or '', 0)
    order = t.get('order')
    if order is not None:
        return (agent, 0, order, t['task_id'])
    return (agent, 1,
            int(t['task_id']) if t['task_id'].isdigit() else 1_000_000,
            t['task_id'])


# ── Session agent roster (whole-session, window-independent) ─────────
# The /live tail is a paged window, so a client-side roster tally silently
# drops agents whose markers sit outside the loaded window — and "ghost"
# agents whose markers were lost to an ingest outage entirely (only their
# agent_id-tagged internal spans survive). Roster truth comes from the
# server over ALL spans; tail spans come from the window; "roster row
# exists but spans not loaded" is a designed state the client renders.

_AGENT_MARKER_NAMES = ('subagent.start', 'subagent.stop')
# Markers plus the launch span — the main timeline's representation of a
# subagent, never part of its internal span count.
_AGENT_SCOPE_MARKER_NAMES = _AGENT_MARKER_NAMES + ('tool.Agent',)


def _in_placeholders(names) -> str:
    return ','.join('?' for _ in names)


def _row_attrs(raw) -> dict:
    try:
        parsed = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _roster_rows(trace_id: str):
    """(activity rows, marker rows, launch rows, session ended).

    ONE grouped pass over the indexed agent_id column feeds BOTH the roster
    badge and the phase verdict — the main bucket (aid IS NULL) is included so
    the phase needn't re-scan. Each row also carries `perm_ts`/`ask_ts` (newest
    PENDING permission / ask-or-plan span), so the waiting-on-a-human check
    reuses the same rows instead of a second GROUP BY.

    PENDING placeholders count toward `last_ts` — a live agent mid-way through a
    long tool has only its placeholder as fresh activity, and dropping it would
    stale a genuinely-running agent — but stay OUT of `internal_count` (the
    append-only store keeps resolved placeholders, so counting them inflates
    span_count).

    `last_key` names the agent's newest STEP as `<start_time>#<padded id>`, the
    same span-time-then-ingest-id ordering `_marker_order` uses and for the same
    reason: `assistant_response`/`assistant.thinking` have no hook and arrive in
    a transcript-rescan batch, so they carry the highest ids in the session
    while sitting EARLIER in time than the live tool they precede. Picking the
    newest by id alone made a running agent's card say "Thinking" while its
    timeline showed the shell command actually in flight. The composite is one
    string in a pass this query already makes — SQLite has no `max_by`, and a
    correlated per-agent lookup is the most expensive read on the trace view."""
    from lib.orm.engine import get_connection

    from lib.trace.trace_service import projection_cache

    scope_ph = _in_placeholders(_AGENT_SCOPE_MARKER_NAMES)
    marker_ph = _in_placeholders(_AGENT_MARKER_NAMES)
    conn = get_connection()
    try:
        # These four reads are a pure function of the stored rows — the
        # clock-dependent part of the roster is `_roster_status`, which runs on
        # the RESULT and stays live. Memoising here keeps a badge that stales
        # on age alone honest while dropping the per-poll `json_extract` scan
        # over every span's attributes (the single most expensive query on the
        # trace view's 4s reconcile).
        key = ('roster', trace_id,
               projection_cache.fingerprint(conn, [trace_id]))
        hit = projection_cache.get(key)
        if hit is not None:
            return hit
        act = conn.execute(
            f"SELECT {AGENT_ID_SQL} AS aid, "
            "       MIN(start_time) AS first_ts, MAX(start_time) AS last_ts, "
            f"      SUM(CASE WHEN name IN ({scope_ph}) OR status_code = 'PENDING' "
            "                THEN 0 ELSE 1 END) AS internal_count, "
            "       MAX(start_time || '#' || printf('%012d', id)) AS last_key, "
            "       MAX(json_extract(attributes,'$.agent_type')) AS agent_type, "
            "       MAX(CASE WHEN status_code = 'PENDING' "
            "                 AND (name = 'permission.request' OR span_id LIKE 'permreq-%') "
            "                THEN start_time END) AS perm_ts, "
            "       MAX(CASE WHEN status_code = 'PENDING' "
            "                 AND name IN ('tool.AskUserQuestion','tool.ExitPlanMode') "
            "                THEN start_time END) AS ask_ts "
            "  FROM session_spans "
            " WHERE trace_id = ? "
            " GROUP BY aid",
            (*_AGENT_SCOPE_MARKER_NAMES, trace_id)).fetchall()
        marks = conn.execute(
            "SELECT id, span_id, name, start_time, attributes FROM session_spans "
            f" WHERE trace_id = ? AND name IN ({marker_ph}) "
            " ORDER BY id ASC",
            (trace_id, *_AGENT_MARKER_NAMES)).fetchall()
        launches = conn.execute(
            "SELECT id, span_id, status_code, start_time, end_time, attributes "
            "  FROM session_spans WHERE trace_id = ? AND name = 'tool.Agent' "
            " ORDER BY id ASC",
            (trace_id,)).fetchall()
        sess = conn.execute(
            'SELECT status FROM sessions WHERE trace_id = ?',
            (trace_id,)).fetchone()
        last_spans = _last_span_rows(
            conn, [i for i in (_last_step_id(r['last_key']) for r in act
                               if r['aid']) if i])
    finally:
        conn.close()
    parsed_launches = [
        {'span_id': r['span_id'], 'status_code': r['status_code'],
         'ts': _roster_ts(r['start_time']), 'end': r['end_time'],
         'attrs': _row_attrs(r['attributes'])}
        for r in launches
    ]
    ended = bool(sess and sess['status'] == 'ended')
    rows = (_with_last_spans(act, last_spans), marks, parsed_launches, ended)
    projection_cache.put(key, rows, (trace_id,))
    return rows


# What a subagent card can say about the agent's newest span: enough to name
# the step without the span body. `preview` is the tool's own headline —
# command, file, or launch description — and empty when the span has none.
_LAST_SPAN_PREVIEW_KEYS = ('command_preview', 'file_path', 'description',
                           'pattern', 'query', 'url')


def _last_step_id(last_key) -> int | None:
    """The span id inside a `<start_time>#<padded id>` composite, or None."""
    if not isinstance(last_key, str) or '#' not in last_key:
        return None
    try:
        return int(last_key.rsplit('#', 1)[1])
    except ValueError:
        return None


def _last_span_rows(conn, ids: list[int]) -> dict[int, dict]:
    """id → the newest span of each agent, as the roster's `last_span`.

    A marker (`subagent.start`/`subagent.stop`) is the parent's account of the
    agent, not a step it took, so an agent whose newest row is its own stop
    marker reports no last span rather than "subagent.stop".
    """
    if not ids:
        return {}
    ph = ','.join('?' * len(ids))
    rows = conn.execute(
        "SELECT id, name, status_code, start_time, attributes "
        f"  FROM session_spans WHERE id IN ({ph})", ids).fetchall()
    out: dict[int, dict] = {}
    for r in rows:
        if r['name'] in _AGENT_MARKER_NAMES:
            continue
        a = _row_attrs(r['attributes'])
        preview = next((a[k] for k in _LAST_SPAN_PREVIEW_KEYS
                        if isinstance(a.get(k), str) and a[k]), '')
        out[r['id']] = {
            'name': r['name'], 'status_code': r['status_code'],
            'start_time': r['start_time'], 'preview': preview[:120],
        }
    return out


def _with_last_spans(act, last_spans: dict[int, dict]) -> list[dict]:
    """The grouped activity rows as dicts, each carrying its `last_span`."""
    out = []
    for r in act:
        d = dict(r)
        d['last_span'] = last_spans.get(_last_step_id(d.get('last_key')))
        out.append(d)
    return out


def _waiting_from_activity(row) -> str | None:
    """Newest PENDING ask/permission timestamp for one agent — the later of the
    row's `perm_ts`/`ask_ts`. `_roster_entry_base` compares it against the
    agent's newest span to decide whether it is blocked on a human."""
    cands = [t for t in (row['perm_ts'], row['ask_ts']) if t]
    if not cands:
        return None
    return max(cands, key=lambda t: _roster_ts(t) or datetime.min)


def _marker_order(m) -> tuple:
    """Span-time ordering with ingest-id tiebreak. The raw store's marker
    times are authoritative here (unlike the client's merge-rewritten view),
    and a REPAIRED marker (reconstruct_subagent_markers) is ingested long
    after the events it represents — id-only ordering would let a
    synthesized start float past the real stop that closes it."""
    ts = _roster_ts(m['start_time'])
    return (ts or datetime.min, m['id'])


def _roster_segments(marks) -> dict:
    """agent_id → {'starts': [...], 'stops': [...]}, each time-ordered."""
    seg: dict[str, dict] = {}
    for r in marks:
        a = _row_attrs(r['attributes'])
        aid = a.get('agent_id')
        if not aid:
            continue
        entry = seg.setdefault(aid, {'starts': [], 'stops': []})
        key = 'starts' if r['name'] == 'subagent.start' else 'stops'
        entry[key].append({'id': r['id'], 'span_id': r['span_id'],
                           'start_time': r['start_time'], 'attrs': a})
    for entry in seg.values():
        entry['starts'].sort(key=_marker_order)
        entry['stops'].sort(key=_marker_order)
    return seg


def _roster_latest_segment(entry):
    """(latest_start, matching_stop). Segment pairing — a resumed agent
    re-starts under the SAME agent_id, so the stop that closes a segment is
    the first one ordered at/after its start; an agent whose start marker
    was lost may still have a stop (start-less segment)."""
    starts, stops = entry['starts'], entry['stops']
    if not starts:
        return None, (stops[-1] if stops else None)
    latest = starts[-1]
    key = _marker_order(latest)
    stop = next((x for x in stops if _marker_order(x) >= key), None)
    return latest, stop


def _launch_type(attrs) -> str | None:
    ti = attrs.get('tool_input')
    return attrs.get('subagent_type') or (
        ti.get('subagent_type') if isinstance(ti, dict) else None)


def _launch_resolved(launch) -> bool:
    """True when a `tool.Agent` launch cleanly completed: `OK` (never
    `PENDING`/`ERROR`), not `denied`, and carrying an `end_time` — the Task
    call it represents only resolves once the subagent it spawned finished, so
    this is proof of completion even when that agent's own `subagent.stop`
    marker was lost. A denied/`ERROR` launch is the interrupted signal
    (`_roster_interrupted`), not this one."""
    if launch['attrs'].get('denied'):
        return False
    return launch['status_code'] == 'OK' and bool(launch.get('end'))


def _launch_for_tu(launches, tu):
    """The non-error launch (the PENDING placeholder) sharing a deny's
    tool_use_id — the deny's only trustworthy anchor."""
    if not tu:
        return None
    for r in launches:
        if r['status_code'] == 'ERROR' or r['attrs'].get('denied'):
            continue
        if r['attrs'].get('tool_use_id') == tu:
            return r
    return None


def _anchored_start(open_entries, launch, taken):
    """The start this launch actually spawned: the nearest same-type start
    at/after the launch's own timestamp."""
    typ = _launch_type(launch['attrs'])
    lts = launch['ts']
    if not typ or not lts:
        return None
    best, best_dt = None, None
    for e in open_entries:
        if e['agent_id'] in taken or e['agent_type'] != typ:
            continue
        if not e['start_ts'] or e['start_ts'] < lts:
            continue
        dt = (e['start_ts'] - lts).total_seconds()
        if best is None or dt < best_dt:
            best, best_dt = e, dt
    return best


def _roster_interrupted(launches, open_entries) -> set[str]:
    """agent_ids whose launch was denied/killed. A deny claims ONLY through
    its anchor chain — deny tool_use_id → the launch placeholder sharing it
    → the start that launch spawned. An unanchored deny (its agent never
    started) claims nothing: guessing by nearest-same-type could mark an
    innocent running agent interrupted."""
    out: set[str] = set()
    for r in launches:
        if r['status_code'] != 'ERROR' and not r['attrs'].get('denied'):
            continue
        launch = _launch_for_tu(launches, r['attrs'].get('tool_use_id'))
        if launch is None:
            continue
        best = _anchored_start(open_entries, launch, out)
        if best:
            out.add(best['agent_id'])
    return out


class _EveryType:
    """Set-like that contains every agent_type. An unresolved launch that names
    no `subagent_type` (Kimi omits it whenever the call didn't pass one) could
    have spawned ANY still-open agent, so no type is safely attributable."""

    def __contains__(self, _agent_type) -> bool:
        return True


def _ambiguous_launch_types(launches):
    """agent_types whose implicit-stop attribution is unsafe: any type with a
    `tool.Agent` launch that has NOT cleanly resolved (still PENDING, or
    ERROR/denied). Only when EVERY same-type launch resolved is the launch↔agent
    mapping unambiguous — see `_apply_implicit_stops`. An unresolved launch of
    UNKNOWN type poisons every type: it is exactly the running agent the
    heuristic must not convict."""
    resolved_by_type: dict = {}
    for r in launches:
        typ = _launch_type(r['attrs'])
        if not typ:
            if not _launch_resolved(r):
                return _EveryType()
            continue
        resolved_by_type[typ] = (
            resolved_by_type.get(typ, True) and _launch_resolved(r))
    return {t for t, ok in resolved_by_type.items() if not ok}


def _apply_implicit_stops(launches, open_entries, interrupted_ids) -> None:
    """Give a still-open agent (start marker, no `subagent.stop`) an implicit
    stop derived from its resolved `tool.Agent` launch's `end_time`, so a lost
    `subagent.stop` doesn't strand a finished agent showing 'running'.

    No launch→agent_id identity link exists — the launch span carries the
    Task's `tool_use_id` but not the resulting `agent_id`; the `subagent.start`
    marker carries `agent_id` but not that `tool_use_id` — so a launch can only
    be tied to an entry by same-type proximity, which is ambiguous while a
    same-type sibling is still running (nearest-start can convict the wrong,
    genuinely-running agent). We therefore REFUSE under ambiguity: no open entry
    of a type with any unresolved same-type launch is demoted (it stays running
    until a real stop lands or it goes stale). Only when every same-type launch
    has resolved is each open entry provably some resolved launch's agent, and we
    stamp each from its nearest resolved launch. Trade-off: a finished agent may
    show 'running' longer, but a running agent is never shown 'finished' — the
    far milder error. An `interrupted` agent is also skipped (`_roster_status`
    checks `stop` before `interrupted`, so this must not stomp that verdict)."""
    ambiguous = _ambiguous_launch_types(launches)
    eligible = [
        e for e in open_entries
        if e['agent_id'] not in interrupted_ids
        and e['agent_type'] not in ambiguous
    ]
    taken: set[str] = set()
    for r in launches:
        if not _launch_resolved(r):
            continue
        best = _anchored_start(eligible, r, taken)
        if best is None:
            continue
        taken.add(best['agent_id'])
        best['stop'] = {'span_id': None, 'start_time': r['end'], 'attrs': {}}


def _launch_description(attrs) -> str:
    ti = attrs.get('tool_input')
    return attrs.get('description') or (
        ti.get('description') if isinstance(ti, dict) else '') or ''


def _roster_description(launches, agent_type, start_ts, claimed) -> str:
    """The launch description for one agent: nearest same-type tool.Agent
    launch not yet claimed by another agent (mirrors the client's
    useAgentLaunchMerge pairing)."""
    best, best_dt = None, None
    for r in launches:
        if r['span_id'] in claimed or _launch_type(r['attrs']) != agent_type:
            continue
        if not _launch_description(r['attrs']):
            continue
        dt = abs((r['ts'] - start_ts).total_seconds()) \
            if (r['ts'] and start_ts) else 0.0
        if best is None or dt < best_dt:
            best, best_dt = r, dt
    if not best:
        return ''
    claimed.add(best['span_id'])
    return _launch_description(best['attrs'])


def _entry_agent_type(latest, stop, act) -> str:
    for src in (latest, stop):
        typ = src['attrs'].get('agent_type') if src else None
        if typ:
            return typ
    return act.get('agent_type') or 'agent'


def _entry_marker_facts(latest, stop) -> dict:
    attrs = latest['attrs'] if latest else {}
    # Workflow-run markers carry the full task prompt (no hook-truncated
    # preview) — clip it to preview size rather than surfacing nothing.
    preview = attrs.get('prompt_preview') or (attrs.get('prompt') or '')[:400]
    return {
        'start_span_id': latest['span_id'] if latest else None,
        'description': attrs.get('description') or attrs.get('label') or '',
        'prompt_preview': preview,
        'marker_seen': (stop or latest or {}).get('start_time'),
    }


def _roster_entry_base(aid, seg_entry, activity, waiting) -> dict:
    """Segment + activity facts for one agent, before status/description."""
    latest, stop = _roster_latest_segment(
        seg_entry or {'starts': [], 'stops': []})
    act = activity.get(aid) or {}
    facts = _entry_marker_facts(latest, stop)
    # `started_at` anchors the LIVE elapsed, so it stays on the current run.
    # How long the agent took overall is a different question, and a resumed
    # one answers it from its first run — see `_roster_output_entry`.
    starts = (seg_entry or {}).get('starts') or []
    started_at = latest['start_time'] if latest else act.get('first_ts')
    last_seen = act.get('last_ts') or facts['marker_seen']
    # Blocked on a human: the newest PENDING ask/permission IS the agent's
    # newest span (a later resolved span means the ask was answered).
    ask_ts = _roster_ts(waiting.get(aid))
    seen_ts = _roster_ts(last_seen)
    return {
        'agent_id': aid,
        'agent_type': _entry_agent_type(latest, stop, act),
        'started_at': started_at,
        'start_ts': _roster_ts(started_at),
        'stop': stop,
        'start_span_id': facts['start_span_id'],
        'first_start_ts': _roster_ts(starts[0]['start_time']) if starts else None,
        'last_seen': last_seen,
        'span_count': int(act.get('internal_count') or 0),
        'description': facts['description'],
        'prompt_preview': facts['prompt_preview'],
        'waiting': bool(ask_ts and seen_ts and ask_ts >= seen_ts),
        'last_span': act.get('last_span'),
        # Workflow-run ingest records completion as `state: done` on the start
        # marker itself — those agents never get a subagent.stop span.
        'declared_done': bool(latest and latest['attrs'].get('state') == 'done'),
    }


def _roster_status(entry, interrupted_ids, now, ended) -> str:
    if entry['stop'] or entry.get('declared_done'):
        return 'finished'
    if entry['agent_id'] in interrupted_ids:
        return 'interrupted'
    # An ended session cannot have a running (or waiting) agent — an
    # unstopped one is stale immediately.
    if ended:
        return 'stale'
    if entry['waiting']:
        return 'waiting'
    # Age alone stales an unstopped agent: a genuinely-running one always
    # has fresh activity — resolved spans or PENDING placeholders, both of
    # which advance last_ts — so 10 min of total silence means dead, even
    # when the agent's last span is the newest thing in the session.
    seen = _roster_ts(entry['last_seen'])
    if seen and (now - seen).total_seconds() > _AGENT_STALE_AFTER_SEC:
        return 'stale'
    return 'running'


def _roster_output_entry(e, status, launches, claimed) -> dict:
    stop_ts = _roster_ts(e['stop']['start_time']) if e['stop'] else None
    # From the agent's FIRST run, not its current one: a resumed agent's last
    # segment is minutes of a run that took an hour, and the sheet presents
    # this as how long the agent took. Identical for the single-run majority.
    began = e.get('first_start_ts') or e['start_ts']
    duration_ms = None
    if stop_ts and began:
        duration_ms = max(0, int((stop_ts - began).total_seconds() * 1000))
    result_preview = e['stop']['attrs'].get('result_preview') if e['stop'] else ''
    return {
        'agent_id': e['agent_id'],
        'agent_type': e['agent_type'],
        'description': e['description'] or _roster_description(
            launches, e['agent_type'], e['start_ts'], claimed),
        'status': status,
        'started_at': e['started_at'],
        'last_seen': e['last_seen'],
        'duration_ms': duration_ms,
        'result_preview': result_preview or '',
        'start_span_id': e['start_span_id'],
        'span_count': e['span_count'],
        'prompt_preview': e['prompt_preview'],
        'last_span': e.get('last_span'),
    }


def _waiting_map(act_rows) -> dict:
    """agent_id → newest PENDING ask/permission ts, for subagents that have
    one. Derived from the shared rows' perm_ts/ask_ts (no extra query)."""
    out: dict = {}
    for r in act_rows:
        w = _waiting_from_activity(r) if r['aid'] else None
        if w:
            out[r['aid']] = w
    return out


def _build_roster(act_rows, marks, launches, ended) -> list[dict]:
    """Assemble roster entries from the shared grouped rows. `waiting` (newest
    PENDING ask/permission per agent) is derived from each row's perm_ts/ask_ts
    rather than a separate query."""
    activity = {r['aid']: dict(r) for r in act_rows if r['aid']}
    waiting = _waiting_map(act_rows)
    segments = _roster_segments(marks)
    entries = [
        _roster_entry_base(aid, segments.get(aid), activity, waiting)
        for aid in set(activity) | set(segments)
    ]
    entries.sort(key=lambda e: e['started_at'] or '')
    open_entries = [e for e in entries if not e['stop']]
    interrupted = _roster_interrupted(launches, open_entries)
    _apply_implicit_stops(launches, open_entries, interrupted)
    now = datetime.now()
    claimed: set[str] = set()
    return [
        _roster_output_entry(
            e, _roster_status(e, interrupted, now, ended),
            launches, claimed)
        for e in entries
    ]


def _roster_with_activity(trace_id: str) -> tuple:
    """(roster, per-agent activity map incl. main under None, ended) from ONE
    grouped pass. The activity map feeds the phase verdict so it needn't
    re-scan session_spans a third time per poll.

    Roster: one entry per agent_id, from marker segments (ingest-id ordered)
    plus orphan agents whose markers were lost but whose agent_id-tagged spans
    survive. Status mirrors the client rules: running / finished / interrupted
    (deny markers) / stale (silent past the stale window while the main agent
    moved on), evaluated with server time."""
    act_rows, marks, launches, ended = _roster_rows(trace_id)
    activity = {r['aid']: dict(r) for r in act_rows}
    if not act_rows and not marks:
        return [], activity, ended
    roster = _build_roster(act_rows, marks, launches, ended)
    _fill_workflow_agent_descriptions(trace_id, roster)
    return roster, activity, ended


def _fill_workflow_agent_descriptions(trace_id: str, roster: list) -> None:
    """Fill description-less workflow-subagent entries with their script
    label and task prompt, joined from the captured run trace. No-op when
    nothing is missing or the run trace isn't ingested yet."""
    def needs_fill(e):
        return e['agent_type'] == 'workflow-subagent' and not e['description']
    if not any(needs_fill(e) for e in roster):
        return
    from lib.trace.workflow_labels import workflow_agent_attrs
    agent_attrs = workflow_agent_attrs(trace_id)
    for e in roster:
        src = agent_attrs.get(e['agent_id'])
        if not needs_fill(e) or not src:
            continue
        e['description'] = src.get('label') or e['description']
        if not e['prompt_preview'] and src.get('prompt'):
            e['prompt_preview'] = src['prompt'][:400]


# ── Per-agent phase (server verdict) ─────────────────────────────────
# The /live card renders phase; it never re-derives state. `phase` is the
# session rollup; `agent_phase` maps "main" (agent_id IS NULL) + each
# subagent id → its phase. Constants live here (item 4): the client keeps
# only its poll cadence + cosmetic ticks. WORKING_WINDOW mirrors the client's
# old IDLE_ACTIVITY floor; INACTIVE_THRESHOLD reuses the roster stale window.
WORKING_WINDOW_SEC = 12
IDLE_SETTLE_SEC = 6
INACTIVE_THRESHOLD_SEC = _AGENT_STALE_AFTER_SEC

# Rollup precedence: a blocked subagent surfaces while main is idle.
# `starting` and `stopping` are reachable only for a run regin owns — nothing
# span-derived can see either — and rank beside `working` because both mean the
# session is busy with something the operator cannot type into.
_PHASE_PRECEDENCE = (
    'waiting-permission', 'waiting-input', 'working', 'starting', 'stopping',
    'idle', 'inactive-stale', 'ended',
)


def _waiting_kind(act) -> str | None:
    """'permission' | 'input' | None — the agent is blocked on a human iff a
    PENDING permission/ask is its NEWEST span (a later resolved span means the
    blocker was answered, which also makes it demotable in merge.py)."""
    last = _roster_ts(act.get('last_ts'))
    perm = _roster_ts(act.get('perm_ts'))
    ask = _roster_ts(act.get('ask_ts'))
    if perm and (last is None or perm >= last):
        return 'permission'
    if ask and (last is None or ask >= last):
        return 'input'
    return None


def _idle_phase(dt, ended) -> str:
    """Non-blocked main phase from activity age alone."""
    if ended:
        return 'ended'
    if dt is None:
        return 'idle'
    if dt > INACTIVE_THRESHOLD_SEC:
        return 'inactive-stale'
    return 'working' if dt <= WORKING_WINDOW_SEC else 'idle'


def _main_phase(act, now, ended) -> str:
    """Main-agent phase. Only `ended` demotes a blocker.

    Silence is NOT evidence against a blocker that is the session's newest
    span: the agent cannot emit anything until a human answers, so the stale
    window measures the wait rather than the session's health. Ageing it out
    turned every question parked longer than INACTIVE_THRESHOLD_SEC into
    'quiet' — the one verdict that hides the sessions this surface exists to
    surface. `_roster_entry_status` has always ranked a subagent's `waiting`
    ahead of its age gate; main now agrees.

    The cost is deliberate: a session hard-killed while parked keeps claiming
    attention until `session.end` arrives or it is closed by hand."""
    last = _roster_ts(act.get('last_ts')) if act else None
    dt = (now - last).total_seconds() if last else None
    kind = _waiting_kind(act) if act else None
    if kind and not ended:
        return 'waiting-permission' if kind == 'permission' else 'waiting-input'
    return _idle_phase(dt, ended)


def _phase_from_roster(status, kind) -> str:
    """Map a subagent roster status into the phase vocabulary for the rollup."""
    if status in ('finished', 'interrupted'):
        return 'ended'
    if status == 'stale':
        return 'inactive-stale'
    if status == 'waiting':
        return 'waiting-permission' if kind == 'permission' else 'waiting-input'
    return 'working'


def _phase_rollup(phases) -> str | None:
    present = set(phases)
    for p in _PHASE_PRECEDENCE:
        if p in present:
            return p
    return None


# The phases a terminal screen is allowed to overrule — the ones derived from
# activity age alone, which is exactly the evidence a hook-silent stretch
# starves.
_SCREEN_ARBITRABLE = ('working', 'idle', 'inactive-stale')


def _screen_phase(state, span_phase: str) -> str | None:
    """Main-agent phase from the terminal's own status line, or None.

    A busy screen is the only evidence that survives a hook-silent stretch:
    spans stop while the agent thinks, streams, or sits inside one long tool
    call, so `_main_phase` reads `idle` within WORKING_WINDOW_SEC of a session
    plainly at work. The rendered screen says so directly.

    The reverse is deliberately NOT symmetric. `busy=False` means "no status
    line was recognised above the composer", which a build whose status line
    this parser has not learned yet produces just as readily as a session
    genuinely at the prompt — and an authoritative `idle` over a span that
    says `working` would reinstate the exact bug this exists to remove, with
    more authority than the heuristic it overrode. So a quiet screen may only
    clear `inactive-stale`: a readable claude pane proves the session is
    ALIVE, which is all that verdict was ever claiming otherwise."""
    if state is None:
        return None
    if state.busy:
        return 'working'
    return 'idle' if span_phase == 'inactive-stale' else None


def _session_phase(by_aid: dict, roster, ended: bool,
                   main_override: str | None = None,
                   screen=None) -> tuple:
    """(rollup phase, agent_phase map). Main from its own activity; each
    subagent from its roster status. `by_aid` is the shared per-agent activity
    map (main under None) so no third scan is needed.

    `screen` is the parsed terminal frame (`agent_bridge.screen_state`) when
    one could be read, and outranks the span-derived verdict for the same
    reason `main_override` does — see `_screen_phase`.

    `main_override` is the live runner's own verdict for a session regin
    launched (`agent_sdk.run_phase`), and it *replaces* the span-derived one
    rather than competing with it: the runner knows, and the heuristic's
    staleness window is exactly wrong for it — a long tool call emits no spans,
    so an agent plainly at work reads `inactive-stale`. Subagents keep their
    roster-derived phases either way; the roster is the only source for those.

    Rollup = highest-precedence phase across all agents, EXCEPT that an ended
    session always rolls up to 'ended': once the session itself ended nothing
    can be genuinely waiting, so a ghost unstopped subagent (rolled up as
    inactive-stale) must not mask the 'ended' verdict."""
    now = datetime.now()
    span_phase = _main_phase(by_aid.get(None), now, ended)
    # The screen only arbitrates between working/idle/inactive-stale. It never
    # overrides 'ended' (a settled session's pane can still show the last frame
    # of a finished turn) nor either 'waiting-' verdict, which come from a
    # structured hook payload and beat reading a menu off the terminal.
    screen_phase = (_screen_phase(screen, span_phase)
                    if span_phase in _SCREEN_ARBITRABLE else None)
    main = main_override or screen_phase or span_phase
    agent_phase = {'main': main}
    for r in roster:
        aid = r['agent_id']
        agent_phase[aid] = _phase_from_roster(
            r['status'], _waiting_kind(by_aid.get(aid) or {}))
    if agent_phase['main'] == 'ended':
        return 'ended', agent_phase
    # A runner reporting `idle` has no turn in flight, and subagents execute
    # INSIDE a turn — so a roster still claiming one is RUNNING is a ghost (a
    # lost `subagent.stop`), and the ordinary rollup would let it outrank the
    # runner's honest `idle`. Drop those votes when the runner says nothing is
    # executing.
    #
    # Only the `working` votes. A `waiting-` subagent is a PARK, which outlives
    # the turn that opened it and is exactly what the switcher exists to
    # surface — silence after a blocker measures the wait, not the session's
    # health. And the roster stays the only source for each subagent's own
    # entry in `agent_phase` either way; this narrows what votes, not what is
    # reported per agent.
    votes = agent_phase.values()
    if main_override == 'idle':
        votes = [p for aid, p in agent_phase.items()
                 if aid == 'main' or p != 'working']
    return _phase_rollup(votes) or agent_phase['main'], agent_phase


def _pct(value, window) -> float | None:
    """`value / window` as a percentage (1 dp), or None when not computable."""
    if isinstance(value, int) and window and window > 0:
        return round(value * 100.0 / window, 1)
    return None


def _workflow_total_tokens(trace_id: str) -> int | None:
    """A workflow run's authoritative grand total (manifest ``totalTokens``)
    from its run-root span; None for non-workflow sessions."""
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT json_extract(attributes, '$.total_tokens') FROM session_spans "
            "WHERE trace_id = ? AND name = 'session.start' "
            "AND json_extract(attributes, '$.total_tokens') IS NOT NULL LIMIT 1",
            (trace_id,)).fetchone()
    finally:
        conn.close()
    return int(row[0]) if row and row[0] is not None else None


def _session_repo_name(trace_id: str, cwd) -> str | None:
    """Short repo label for the session header: the primary registered repo
    name, falling back to the basename of the session cwd."""
    from sqlmodel import select as _sel
    from lib.orm.models import Repo, SessionRepo
    with SessionLocal() as session:
        name = session.exec(
            _sel(Repo.name)
            .join(SessionRepo, SessionRepo.repo_id == Repo.id)
            .where(SessionRepo.trace_id == trace_id)
            .order_by(SessionRepo.is_primary.desc(), Repo.name.asc())
        ).first()
    if name:
        return name
    if isinstance(cwd, str) and cwd.strip():
        return os.path.basename(cwd.rstrip('/')) or None
    return None


def _owned_run_phase(trace_id: str) -> str | None:
    """The live runner's own phase for a run regin launched, else None.

    Best-effort like every other SDK read on this path: the tier is optional
    and gated off by default, and a session's status line must not depend on it
    being importable.
    """
    try:
        from lib import agent_sdk
        return agent_sdk.run_phase(trace_id)
    except Exception:
        return None


def _live_child_of(trace_id: str) -> str | None:
    """The still-running child of a run whose session `trace_id` names is over.

    `/clear` retires a child and opens a new one, so the id the operator is
    holding — the run's own, or the child that was just cleared — describes an
    ended session while the agent keeps working under an id nothing on the page
    points at. Best-effort like every other SDK read on this path.

    Both tiers answer it, because the operator asking is not thinking about
    which one they are on. The SDK registry is asked first and wins: a run
    regin owns holds the fact in process, where a `bridge_panes` row for the
    same session could only ever be a weaker copy of it. The tmux read sits
    behind `agent_bridge.enabled` like every other pane read: with the bridge
    off there is no terminal to send the successor a prompt through, so naming
    one would only be a field the page cannot act on.
    """
    from lib import agent_sdk
    try:
        child = agent_sdk.live_child(trace_id)
    except Exception:
        child = None
    if child:
        return child
    from lib.settings import settings
    if not settings.agent_bridge.enabled:
        return None
    try:
        from lib.agent_bridge import store as bridge_store
        return bridge_store.clear_successor(trace_id)
    except Exception:  # noqa: BLE001 - a summary must never 500 on this
        from lib.activity_log import get_activity_logger
        get_activity_logger("bridge").error(
            "bridge_successor_read_failed", exc_info=True)
        return None


def _screen_state_mod():
    from lib.agent_bridge import screen_state
    return screen_state


def _live_screen_state(trace_id: str, group):
    """The session's live status, or None when there is no verdict.

    Which source answers is decided once, from the tier regin recorded when
    the session was launched: a run regin owns is answered by its runner in
    this process, and every other session by its terminal. The two are never
    merged nor tried in turn — a run's child is spawned with the bridge off
    and has no pane at all, so probing tmux for one spends a subprocess to
    learn nothing. Asked over the whole alias group, since a run is reachable
    under either of the two ids it is traced as.

    Fail-soft by construction: the bridge is off by default and reaches only
    tmux-launched sessions, so "no screen" is the ordinary case, not an error.
    Any failure below the API — bridge disabled, no pane, stale registry row,
    a tmux that will not answer — resolves to None and the caller keeps the
    span-derived phase.
    """
    try:
        from lib import agent_sdk
        for tid in group:
            owned = agent_sdk.run_live_state(tid)
            if owned.sdk:
                return owned.state
        from lib.agent_bridge import delivery
        return delivery.live_state(trace_id, accept=set(group))
    except Exception:  # noqa: BLE001 - a screen read must never fail a summary
        from lib.activity_log import get_activity_logger
        get_activity_logger("bridge").error(
            "live_screen_read_failed", exc_info=True)
        return None


def _record_screen_retry(group, screen) -> None:
    """Persist a retry the screen is showing, so the turn keeps a record of it.

    `live_screen` only ever describes NOW; the span is what the operator can
    still read once the retry has resolved.
    """
    from lib.agent_bridge import retry_events
    retry_events.record_retry(group, screen)


def _subagent_session_summary(trace_id: str) -> dict:
    """The header for a subagent trace, synthesised because no `sessions` row
    exists under an `sa-…` id (see `lib.trace.subagent_trace.subagent_summary`
    for what each field is derived from).

    Deliberately narrower than the ordinary summary: the phase rollup, the
    live screen read and the steer-channel fields all describe a whole CLI
    session, and a child is a slice of one — reporting them here would answer
    for the parent under the child's name.
    """
    from lib.orm.engine import get_connection
    from lib.trace.subagent_trace import subagent_summary
    conn = get_connection()
    try:
        return subagent_summary(conn, trace_id) or {}
    finally:
        conn.close()


def _session_summary(trace_id: str, roster=None, activity=None) -> dict:
    """Read the session-row fields that aren't derivable from spans alone
    (model + transcript-derived token counters) plus the server phase verdict.
    Safe to call for a non-existent trace_id — returns an empty dict.

    `roster` + `activity` (the whole-session agent roster and its shared
    per-agent activity map) are threaded from the shallow-map hot path so the
    phase rollup reuses them instead of recomputing; other callers pass None
    and both are computed here in one grouped pass.
    """
    from sqlmodel import select as _select
    from lib.tokens.model_windows import infer_window as _infer_window
    from lib.trace.subagent_trace import is_subagent_trace_id
    if is_subagent_trace_id(trace_id):
        return _subagent_session_summary(trace_id)
    group = _alias_group_ids(trace_id)
    with SessionLocal() as session:
        row = session.exec(
            _select(
                SessionModel.model,
                SessionModel.cwd,
                SessionModel.input_tokens, SessionModel.output_tokens,
                SessionModel.cache_read_tokens, SessionModel.cache_creation_tokens,
                SessionModel.peak_context_tokens,
                SessionModel.peak_main_context_tokens,
                SessionModel.live_context_tokens,
                SessionModel.active_work_ms,
                SessionModel.started_at,
                SessionModel.ended_at,
                SessionModel.last_seen,
                SessionModel.status,
                SessionModel.ended_reason,
                SessionModel.title,
                SessionModel.title_source,
                SessionModel.origin,
            )
            # Prefer the canonical row, but fall back to whichever member of
            # the group HAS one. A regin-launched run whose child never wrote a
            # hook trace — an install where regin's hooks aren't wired for that
            # cwd — has no canonical row at all, and keying on it blindly wiped
            # the whole header (status, title, model, phase all null) for every
            # such run.
            .where(SessionModel.trace_id.in_(group))
            .order_by((SessionModel.trace_id == group[0]).desc())
        ).first()
    if not row:
        return {}
    (model, cwd, input_tokens, output_tokens,
     cache_read, cache_creation, peak, peak_main, live, active_work_ms,
     started_at, ended_at, last_seen, status, ended_reason,
     title, title_source, origin) = row
    ended_reason = _group_ended_reason(group, ended_reason)
    # Compute window at read time from the session's richer `model` id —
    # see _row_to_dict() for the rationale. Window inference uses the
    # all-inclusive peak; the headline `context_pct` divides the *live*
    # peak by it (main-flow high-water mark since the last `/compact`, so
    # it drops when the session compacts), `context_pct_all` divides the
    # full peak (shown alongside only when they diverge).
    window = _infer_window(model, peak) if isinstance(peak, int) else None
    main_for_pct = next(
        (v for v in (live, peak_main, peak) if isinstance(v, int)), peak)
    if roster is None or activity is None:
        roster, activity, _ended = _roster_with_activity(trace_id)
    screen = _live_screen_state(trace_id, group)
    _record_screen_retry(group, screen)
    phase, agent_phase = _session_phase(activity, roster, status == 'ended',
                                        _owned_run_phase(trace_id), screen)
    return {
        # What the real terminal says is happening right now — the verb,
        # turn elapsed, tokens streamed and in-flight tool the operator would
        # read off the pane. Null whenever the screen could not be read, which
        # the card renders as "open the terminal", never as idle.
        'live_screen': _screen_state_mod().as_payload(screen),
        # The id this session is really keyed on — the child's for a
        # regin-launched run. `/live` navigates to the run's own `sdk-…` id at
        # launch (the only id that exists then) and rewrites onto this one as
        # soon as the child names itself, so a URL that is shared, bookmarked
        # or reloaded is the one every other reader already uses.
        'canonical_trace_id': group[0],
        # The child a live run is driving, when that is not the session this id
        # names — so it is set for a run's own id, and for a child `/clear`
        # retired. That second case is the one it exists for: the page the
        # operator is on reads `ended` while the agent works on elsewhere, and
        # the composer stays open on the strength of this (a steer sent to
        # either id reaches the same runner, and on the tmux tier the same
        # pane). Null for every ordinary session.
        'successor_trace_id': _live_child_of(trace_id),
        'model': model,
        # Server phase verdict — the /live card selects on this, never
        # re-derives it. `phase` = session rollup; `agent_phase` = "main"
        # + each subagent id → its phase.
        'phase': phase,
        'agent_phase': agent_phase,
        # The same runner-only verdict the session LIST carries, under the
        # same name, so the header and the row it was opened from resolve
        # their status through one field. Null for a session regin only
        # traces; `phase` above is always present and is the richer,
        # heuristic-backed rollup the /live card renders.
        'runner_phase': _owned_run_phase(trace_id),
        'repo': _session_repo_name(trace_id, cwd),
        # The working directory the session ran in. `repo` is only the short
        # label; the header shows the path itself, which is what tells two
        # checkouts of the same repo apart.
        'cwd': cwd,
        # Workflow runs have no single context window (peak is NULL), so the
        # ctx% chip is absent; surface the run's authoritative grand total
        # (manifest totalTokens, stamped on the run-root span) for a header
        # total chip. None for non-workflow sessions. The per-turn split in
        # the columns below can't be summed to it — cache is counted
        # differently — so it's read straight off the span, not derived.
        'total_tokens': _workflow_total_tokens(trace_id),
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'cache_read_tokens': cache_read,
        'cache_creation_tokens': cache_creation,
        'peak_context_tokens': peak,
        'peak_main_context_tokens': peak_main,
        'live_context_tokens': live,
        'context_window_tokens': window,
        'context_pct': _pct(main_for_pct, window),
        'context_pct_all': _pct(peak, window),
        'active_work_ms': active_work_ms,
        'started_at': started_at,
        'ended_at': ended_at,
        'last_seen': last_seen,
        # status/ended_reason ride the summary so the /live poll can flip a
        # session to "finished" (or back, on resume) without a separate row
        # fetch — the row endpoint alone left the header frozen at its
        # page-load value.
        'status': status,
        'ended_reason': ended_reason,
        # Server wall-clock at read time (naive local, same basis as every
        # span start_time). The /live NOW-zone elapsed anchors to THIS instead
        # of the viewer's Date.now(): span timestamps are the server's local
        # time, so a phone in a different timezone subtracting its own clock
        # leaks the offset (a tool reads "4h00m"). server_now − start_time is
        # server−server, so the offset cancels.
        'server_now': datetime.now().isoformat(),
        # The UTC twin is what serverAgeMs anchors against when the row's own
        # timestamp carries an explicit zone; without it a zoned last_seen is
        # aged against a naive-local anchor and the offset leaks into the age.
        'server_now_utc': datetime.now(timezone.utc).strftime(
            '%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
        'title': title,
        'title_source': title_source,
        # Same axis the sessions list exposes (origin == 'workflow'): a
        # workflow run's turn_usage rows are per-agent API responses, so the
        # trace view must not read them as user-prompt turns.
        'origin': origin,
        'is_workflow': origin == 'workflow',
    }


@trace_bp.route('/api/sessions/<trace_id>/subtrees')
def api_session_subtrees(trace_id):
    """Deep subtrees for several roots in ONE projection.

    The trace view refreshes the trailing roots on every reconcile: children
    are still streaming under the active prompt, and a background agent can
    land spans under an older one. Asked one root at a time, that ran
    `fetch_session_projection` — a whole-session merge — once per root, three
    times over, for one screen's worth of new spans.

    Span bodies are deduplicated across the requested roots, which matters
    because a subagent's spans can hang under more than one of them.
    """
    ids = [i for i in (request.args.get('ids') or '').split(',') if i]
    if not ids:
        return jsonify({'error': 'ids is required'}), 400
    known = _parse_known_watermarks(request.args.get('known'))
    dirty = {i for i in (request.args.get('dirty') or '').split(',') if i}
    seen = _parse_seen_digests(request.args.get('seen'))
    widened, tree = trace_service.fetch_session_projection(
        trace_id, drop_delegated=True)
    by_id = {s['span_id']: s for s in widened}

    subtrees, wanted, unchanged, digests = _collect_subtrees(
        tree, ids, by_id, known, dirty, seen)
    return jsonify({
        'trace_id': trace_id,
        'subtrees': subtrees,
        # Named rather than merely omitted: absent from `subtrees` already
        # means "this root has no children", which the client acts on by
        # clearing them. Silence could not carry both answers.
        'unchanged': unchanged,
        'digests': digests,
        'spans': [by_id[i] for i in wanted if i in by_id],
    })


def _parse_seen_digests(raw) -> dict:
    """`{root_span_id: structure digest the client last applied}`."""
    out: dict = {}
    for pair in (raw or '').split(','):
        root, _, value = pair.partition(':')
        if root and value:
            out[root] = value
    return out


def _collect_subtrees(tree, ids, by_id, known, dirty, seen=None):
    """`({root: wire nodes}, {span ids whose body must ship}, [unchanged roots])`.

    A root absent from the projection is left out of the map entirely; the
    client reads that as "no children" and clears whatever it was holding,
    which is how a retired or reparented root stops lingering in the timeline.

    A root whose structure hashes to what the caller says it already holds, and
    for which no body needs sending, is reported as unchanged instead of being
    serialised again. The refresh asks about the trailing few prompts on every
    reconcile, and the older ones have almost always finished — one of them ran
    to 200 nodes and 16KB of structure, re-sent every few seconds to describe a
    subtree that had not moved in minutes.
    """
    seen = seen or {}
    subtrees: dict = {}
    digests: dict = {}
    wanted: set[str] = set()
    unchanged: list = []
    for span_id in ids:
        children = _find_child_nodes(tree, span_id)
        if children is None:
            continue
        nodes = _wire_nodes(children, deep=True)
        descendants: set[str] = set()
        _collect_descendant_ids(children, descendants)
        watermark = known.get(span_id)
        bodies = {d for d in descendants
                  if _body_needed(by_id.get(d), watermark, dirty)}
        digest = _structure_digest(nodes)
        digests[span_id] = digest
        # Only when there is also nothing to deliver: a subtree can keep its
        # shape while one of its spans grows.
        if not bodies and seen.get(span_id) == digest:
            unchanged.append(span_id)
            continue
        subtrees[span_id] = nodes
        wanted.update(bodies)
    return subtrees, wanted, unchanged, digests


def _structure_digest(nodes) -> str:
    """Stable hash of one subtree's wire shape."""
    payload = json.dumps(nodes, sort_keys=True, separators=(',', ':'))
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


def _parse_known_watermarks(raw) -> dict:
    """`{root_span_id: highest descendant row id the client already holds}`.

    Wire form is `root:id,root:id`. A root the client has never deep-loaded is
    simply absent, which is what makes it receive every body.
    """
    out: dict = {}
    for pair in (raw or '').split(','):
        root, _, value = pair.partition(':')
        if not root or not value.isdigit():
            continue
        out[root] = int(value)
    return out


def _body_needed(span, watermark, dirty: set) -> bool:
    """Does the client need this span's body, or does it already hold it?

    Withheld only when all three hold, so every uncertainty ships the body:

    * the client deep-loaded this subtree past this span's row id — a span it
      has never seen is always above the watermark, because a new span is an
      INSERT and takes a fresh id (an in-place rewrite keeps its id, which is
      why the watermark alone is not the whole answer);
    * the push stream did not name it as changed since;
    * the span has settled. An unfinished span is still being written, and its
      body is the one thing that grows without any identifier moving;
    * the body was not DERIVED from a row the client has yet to read. A
      serve-time join builds one span's body out of another's, so the enriched
      span settles and keeps its id while its body still changes —
      `DERIVED_FROM_ID_KEY` names the row it came from, and the same watermark
      test applied to that id is what makes the refresh both arrive and stop.
    """
    if span is None or watermark is None:
        return True
    if span.get('span_id') in dirty:
        return True
    row_id = span.get('id')
    if row_id is None or row_id > watermark:
        return True
    if _derived_from_id(span, watermark):
        return True
    return not (span.get('end_time') and span.get('status_code') != 'PENDING')


def _derived_from_id(span, watermark) -> bool:
    """Was this span's body built from a row above the client's watermark?"""
    from lib.trace.merge import DERIVED_FROM_ID_KEY
    attrs = span.get('attributes')
    source = attrs.get(DERIVED_FROM_ID_KEY) if isinstance(attrs, dict) else None
    return isinstance(source, int) and source > watermark


def _find_child_nodes(nodes, span_id):
    """The child nodes of `span_id` in a projected tree, or None if absent."""
    for node in nodes:
        if node['data']['span_id'] == span_id:
            return node.get('children', [])
        found = _find_child_nodes(node.get('children', []), span_id)
        if found is not None:
            return found
    return None


def _collect_descendant_ids(nodes, out: set) -> None:
    for node in nodes:
        out.add(node['data']['span_id'])
        _collect_descendant_ids(node.get('children', []), out)


@trace_bp.route('/api/sessions/<trace_id>/spans/<span_id>/children')
def api_session_span_children(trace_id, span_id):
    """Return the children of `span_id`.

    By default (lazy-expand flow) returns direct children only as shallow
    tree nodes. Pass `?deep=1` to get the full subtree with inline
    children — used by the Trace view's reload path to refresh the
    active prompt's subtree without re-fetching the whole session.
    """
    deep = request.args.get('deep', '').lower() in ('1', 'true')
    widened, tree = trace_service.fetch_session_projection(
        trace_id, drop_delegated=True)
    by_id = {s['span_id']: s for s in widened}

    children = _find_child_nodes(tree, span_id) or []
    if deep:
        descendant_ids: set[str] = set()
        _collect_descendant_ids(children, descendant_ids)
        return jsonify({
            'trace_id': trace_id,
            'parent_span_id': span_id,
            'children': _wire_nodes(children, deep=True),
            'spans': [s for i, s in by_id.items() if i in descendant_ids],
        })
    child_ids = {n['data']['span_id'] for n in children}
    return jsonify({
        'trace_id': trace_id,
        'parent_span_id': span_id,
        'children': _shallow_nodes(children),
        'spans': [s for i, s in by_id.items() if i in child_ids],
    })


# Attribute keys the light structural map preserves after the strip:
#  * the `/rewind` signal the frontend needs to collapse the discarded branch
#    and label the marker without a content round-trip;
#  * the compact `memory.recall` labels (`hit_count`, plus `source`/`skill_id`
#    that distinguish an injected `<skill_experience>` block from generic
#    recalled experience) so MemoryRecallRow renders its header durably on a
#    fresh load — not just transiently off the live-tail append.
# The heavier attrs (rewind orphan_keys/rolled_back_files, the recall `block`
# and per-hit `hits` list) are dropped here and loaded lazily via
# /spans/<id>/rewind and /spans/<id>/content.
_MAP_KEEP_ATTR_KEYS = (
    'rewound_away', 'rewind_fork_id',
    'abandoned_prompt_count', 'rolled_back_count',
    'hit_count', 'source', 'skill_id',
    # The per-agent scope key: the frontend's attribute partition (orphan
    # roster entries with no start marker to tree-walk from) matches on it.
    # `label` rides along so a workflow subagent's marker stays nameable
    # after the strip.
    'agent_id', 'label',
    # The whole of what a subagent did, which its marker is now the only
    # row here to say — the spans these summarize are served by the child
    # trace `agent_child_trace_id` addresses.
    'agent_span_count', 'agent_tool_count', 'agent_input_tokens',
    'agent_output_tokens', 'agent_cost_usd', 'agent_duration_ms',
    'agent_child_trace_id',
    # Which run of a RESUMED agent this marker is, and what that run alone
    # did. Without them every segment card reads the one roster entry keyed on
    # `agent_id` and they render identically.
    'agent_segment_index', 'agent_segment_count',
    'agent_segment_span_count', 'agent_segment_tool_count',
    'agent_segment_duration_ms',
    # What THIS run was asked and what it answered — the launch prompt and the
    # result only ever describe the first one.
    'agent_segment_prompt', 'agent_segment_result',
    # The whole-agent answer, for the agents that never resumed — written to
    # the stop marker, carried here so the card can show it.
    'agent_result_preview',
    # `harness.api_retry` renders entirely from these: an attempt count with
    # no denominator, or a row with no wait, is the bare spinner this span was
    # added to replace.
    'attempt', 'max_attempts', 'retry_in',
    # ScheduleWakeup label needs these to distinguish "agent finished" (stop)
    # from "paused to resume" — else the row degrades to a bare tool name on a
    # fresh (non-shallow /map) reload. `resume_action`/`poll_*` are the
    # serve-time cross-turn link (wakeup_links.annotate_wakeup_resumes).
    'stop', 'delay_seconds', 'reason',
    'resume_action', 'resume_span_id', 'poll_round', 'poll_total',
    # An SDK run that continued an earlier session names it on session.start;
    # dropped here, the trace would open with no sign it inherited a history.
    'resumed_from',
)


def _kept_map_attrs(attrs) -> dict:
    """The small subset of a span's attributes the structural map keeps."""
    if not isinstance(attrs, dict):
        return {}
    return {k: attrs[k] for k in _MAP_KEEP_ATTR_KEYS if k in attrs}


# What labels a tool call that has not reported back: the flat preview keys
# `fullLabel`/`RunningSpanCard` read. Deliberately NOT the bag it rides in — a
# pending row also carries `command` (the full, untruncated command),
# `tool_input`, and for `tool.Agent` the whole sub-agent prompt: measured over
# the 4000 newest PENDING rows in the local store, one `tool.Bash` bag reached
# 18 KB. The chip is one truncated line, so the previews say everything it can
# show.
_LIVE_TOOL_ATTR_KEYS = (
    'tool_name', 'live',
    'command_preview', 'description', 'subagent_type',
    'file_path', 'pattern', 'query', 'url',
    'skill_name', 'task_id', 'subject', 'status',
    'reject_reason',
)

# Not every key above is capped at capture — `description` is written through
# raw (`hook_manager/handlers/post_tool_trace.py`). One chip line is the whole
# consumer, so the ceiling is imposed here rather than trusting each writer.
_LIVE_TOOL_ATTR_MAX_CHARS = 400


def _is_live_tool_row(span) -> bool:
    """A tool call still in flight — the one row kind the lazy content read
    cannot answer for in time.

    Every other row is stripped here and filled by /spans/<id>/content once
    the reader asks. An in-flight row has no result to ask for, and the two
    reads are taken at different instants: a row the merge retires between
    them keeps whatever the map gave it, so a stripped one is stuck rendering
    its bare tool name.
    """
    return (span.get('status_code') == 'PENDING'
            and str(span.get('name') or '').startswith('tool.'))


def _kept_live_tool_attrs(attrs) -> dict:
    """`_kept_map_attrs` widened by the in-flight label keys, each capped."""
    if not isinstance(attrs, dict):
        return {}
    live = {}
    for key in _LIVE_TOOL_ATTR_KEYS:
        if key not in attrs:
            continue
        value = attrs[key]
        live[key] = (value[:_LIVE_TOOL_ATTR_MAX_CHARS]
                     if isinstance(value, str) else value)
    return {**_kept_map_attrs(attrs), **live}


# The columns the structural map merges on and ships. A subagent read comes
# through the projection fetch, which selects a wider row (usage counters, the
# agent tag) than this endpoint has ever carried, so it is narrowed to this.
_MAP_SPAN_KEYS = (
    'id', 'trace_id', 'span_id', 'parent_id', 'name', 'kind',
    'start_time', 'end_time', 'duration_ms', 'status_code', 'status_message',
    'attributes', 'turn_uuid', 'tool_use_id', 'source',
)


def _map_source_spans(group: list[str]) -> list[dict]:
    """The rows the structural map merges, however they are addressed.

    Through the projection fetch, which answers for a subagent id (not one of
    its rows is STORED under `sa-…`, so a stored-trace_id query serves an empty
    map) and stamps each subagent marker's rollup — all this view keeps of the
    agent, and so the only row left in it that can say what the agent did.
    Narrowed afterwards to the columns this endpoint has always carried.
    """
    from lib.trace.projection import _fetch_spans_for_group
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        fetched = _fetch_spans_for_group(conn, group)
    finally:
        conn.close()
    return [{**{k: s[k] for k in _MAP_SPAN_KEYS if k in s},
             ORIGIN_KEY: s.get(ORIGIN_KEY, 'hook')} for s in fetched]


def _map_session_activity(stored: list[str]) -> dict | None:
    """`{'status', 'last_seen'}` for the session behind the map, or None."""
    from sqlmodel import select as _select
    with SessionLocal() as session:
        act_row = session.exec(
            _select(SessionModel.status, SessionModel.last_seen)
            .where(SessionModel.trace_id.in_(stored))
            .order_by(SessionModel.last_seen.desc())
        ).first()
    return {'status': act_row[0], 'last_seen': act_row[1]} if act_row else None


def _structural_map_spans(trace_id: str) -> list[dict]:
    """The full structural span list for the non-shallow `/map` (Terminal tab).

    The store is append-only, so a placeholder and its real anchor coexist —
    `merge_spans` selects the winner. It correlates by prompt-text hash /
    tool_name, so it needs `attributes` (and `turn_uuid` for the reparent
    ladder); read them from session_spans rather than the attribute-less
    session_trace_map. After merging we STRIP attributes/turn_uuid so the map
    stays the light, structure-only payload the frontend expects (descendants
    fetch content lazily via /spans/<id>/content)."""
    from lib.trace.merge import merge_spans
    from lib.trace.trace_service.queries import _attach_prompt_expansions

    group = _alias_group_ids(trace_id)
    # The session-level joins follow the STORED ids: a subagent trace has no
    # `sessions` row and no workflow spans of its own, and reading them under
    # `sa-…` would call a live run inactive and leave its markers unlabelled.
    stored = _stored_group_ids(trace_id)
    spans = _map_source_spans(group)
    session_activity = _map_session_activity(stored)
    # `tool_use_id` is fetched only so the cross-source reconcile can join on
    # it; this endpoint's span dict never carried it, and a guard test pins
    # that shape. Dropped with the origin marker rather than widened here.
    grafted = [{k: v for k, v in s.items() if k != 'tool_use_id'}
               for s in strip_origin(
                   merge_spans(spans, session_activity=session_activity))]
    _attach_prompt_expansions(stored[0], grafted)
    from lib.trace.workflow_labels import attach_workflow_agent_attrs
    # Canonical, like the spans it annotates — the URL id would make the two
    # entry points diverge for a session carrying bare workflow markers.
    attach_workflow_agent_attrs(stored[0], grafted)
    from lib.trace.wakeup_links import annotate_wakeup_resumes
    annotate_wakeup_resumes(grafted)
    from lib.trace.projection import drop_delegated_spans
    grafted = drop_delegated_spans(grafted)
    for s in grafted:
        attrs = s.get('attributes')
        kept = (_kept_live_tool_attrs(attrs) if _is_live_tool_row(s)
                else _kept_map_attrs(attrs))
        s.pop('attributes', None)
        s.pop('turn_uuid', None)
        if kept:
            s['attributes'] = kept
    return grafted


def _bridge_reachability(trace_id: str) -> dict:
    """Per-session steerability for the /live composer and answer sheet.

    Rides the existing shallow-map poll so the client needs no extra
    polling loop. Exposes only a boolean + the pane label — never the
    bridge bearer token (that credential must not reach the browser).

    Reachable means "regin can steer this session", not "this session has a
    tmux pane": a session regin launched itself is answered over its typed
    channel and has no pane at all, so gating the sheet on a pane would leave
    the tier's own questions unanswerable.

    A `/clear`ed session is reached through the conversation that replaced it.
    Its own pane read cannot answer — `_SELF_NOT_ENDED` refuses a row whose
    session has ended, and a cleared session has ended by definition — so
    without this fallback the page would carry a `successor_trace_id` it had
    no composer to use. The pane label is the same physical pane either way.

    `resumable` is the other half of the same question, and rides here for the
    same reason: what the page may offer when nothing can steer the session. A
    reachable session is never resumable — continuing a conversation that is
    still running would put two processes on one session id.
    """
    state = _steerability(trace_id)
    # Only the conversation the run is on NOW. Ownership resolves through the
    # run, so every child of a live run reports `sdk_owned` — including ones
    # it genuinely finished with, whose `ended_at` is a true verdict the heal
    # would erase.
    if state['sdk_owned'] and _sdk_driving(trace_id):
        _heal_ended_while_owned(trace_id)
    refusal = '' if state['bridge_reachable'] else _resume_refusal(trace_id)
    state['resumable'] = not state['bridge_reachable'] and not refusal
    # The one refusal a reader needs: neither steerable nor resumable is an
    # empty rail either way, and only this says the conversation is alive.
    state['cli_held'] = refusal == resumable_mod.REFUSED_CLI_HELD
    return state


def _sdk_driving(trace_id: str) -> bool:
    """Whether a run regin holds is on THIS conversation — the registry alone,
    since the tmux tier holds no runs and only the SDK tier can answer it.

    Fails closed: the caller uses this to decide whether to clear an end
    marker, and the cost of a wrong yes is a finished session shown as live.
    """
    from lib import agent_sdk
    try:
        return agent_sdk.is_driving(trace_id)
    except Exception:  # noqa: BLE001 - a map poll must never 500 on this
        from lib.activity_log import get_activity_logger
        get_activity_logger("agent_sdk").error(
            "sdk_driving_probe_failed", trace_id=trace_id, exc_info=True)
        return False


def _heal_ended_while_owned(trace_id: str) -> None:
    """Clear an end marker on a session a run is driving right now.

    Ownership is the stronger evidence: it means the runner object exists in
    THIS process, while `ended_at` is a claim some earlier writer left on the
    row. When they disagree the row is wrong, and the disagreement renders as
    nothing at all — the composer is gated on `!ended_at`, Resume on
    `!bridge_reachable`, and the cli-held notice on neither — so an operator
    meets a live conversation with no way to type into it and no way to
    reopen it.

    Healed rather than patched into this one response: every other reader
    (the session list, the header pill, `/live`'s poll, which stops for good
    on an ended session) answers from the same row and would go on disagreeing
    with the composer beside it. Idempotent — `revive_trace_session` touches
    only a row that claims to be over — so the poll that finds nothing wrong
    writes nothing.

    The whole alias group, for the reason the launch revives both halves: a
    run and the child it drives each own a `sessions` row, and reviving only
    the id this request named leaves the other still ended for whichever
    entry point resolves to it.
    """
    from lib.agent_sdk import store as agent_sdk_store
    try:
        agent_sdk_store.revive_trace_session(*_alias_group_ids(trace_id))
    except Exception:
        from lib.activity_log import get_activity_logger
        get_activity_logger("agent_sdk").error(
            "sdk_ended_while_owned_heal_failed", trace_id=trace_id,
            exc_info=True)


def _resume_refusal(trace_id: str) -> str:
    """`resumable.resume_refusal`, degraded to a generic refusal on failure.

    The check reaches the filesystem (the provider's transcript glob), the tmux
    pane registry and the CLI, none of which the map poll may be broken by: a
    session that renders without a Resume button is a missing affordance, one
    that fails to render at all is a dead page.
    """
    try:
        return resumable_mod.resume_refusal(trace_id) or ''
    except Exception:
        from lib.activity_log import get_activity_logger
        get_activity_logger("agent_sdk").error(
            "sdk_resumable_probe_failed", trace_id=trace_id, exc_info=True)
        return 'probe_failed'


def _steerability(trace_id: str) -> dict:
    """Which tier, if any, can type into `trace_id` right now."""
    from lib import agent_sdk
    from lib.settings import settings
    if agent_sdk.is_sdk_owned(trace_id):
        return {'bridge_reachable': True, 'bridge_pane': None,
                'sdk_owned': True}
    if not settings.agent_bridge.enabled:
        return {'bridge_reachable': False, 'bridge_pane': None,
                'sdk_owned': False}
    from lib.agent_bridge import store as bridge_store
    pane = bridge_store.get_reachable_pane(trace_id)
    if pane is None:
        successor = bridge_store.clear_successor(trace_id)
        if successor:
            pane = bridge_store.get_reachable_pane(successor)
    return {'bridge_reachable': pane is not None,
            'bridge_pane': pane['pane_id'] if pane else None,
            'sdk_owned': False}


def _shallow_map_response(trace_id: str):
    """Paginated structural map for the live trace view (`?shallow=1`): root
    spans + lazy tree nodes + cursors + the merge's `retired_span_ids` (rows the
    serve-time merge dropped from this window, which the client must prune from
    its append-only `session.spans` or the cards show a duplicate)."""
    from sqlmodel import select as _select
    from sqlalchemy import func as _func
    try:
        limit = int(request.args.get('limit', 50))
    except (TypeError, ValueError):
        return jsonify({'error': 'limit must be an integer'}), 400
    limit = max(1, min(limit, 200))
    before_id = request.args.get('before_id', type=int)
    after_id = request.args.get('after_id', type=int)
    if before_id is not None and after_id is not None:
        return jsonify({'error': 'before_id and after_id are mutually exclusive'}), 400

    # Live tail (initial / additive reload, not scroll-up): kick a background
    # transcript rescan so assistant turns flushed since the last hook scan get
    # ingested and show within a poll or two (esp. subagent reasoning, which
    # otherwise batches at SubagentStop). Fire-and-forget; this response still
    # serves the current DB state, the next poll picks up the new turns.
    if before_id is None:
        from lib.trace.live_rescan import trigger_rescan
        trigger_rescan(trace_id)

    from lib.trace.trace_service import projection_cache

    structural = _shallow_structural_half(trace_id, limit, before_id, after_id)
    # The client names the drop-list version it has already applied. Unchanged
    # means it has pruned exactly these rows and re-sending them would only
    # make it re-derive a no-op — so send the tag alone. A version it has NOT
    # seen always arrives in full, which is what keeps a late retirement (a
    # placeholder promoted long after it was loaded) reaching the cards.
    if structural.get('retired_digest') \
            and request.args.get('retired_seen') == structural['retired_digest']:
        structural = {k: v for k, v in structural.items()
                      if k != 'retired_span_ids'}
    roster, activity, _ended = _roster_with_activity(trace_id)
    return jsonify({
        'trace_id': trace_id,
        # The authoritative count of span changes, against which the client
        # checks the push frames it received. It is what makes the frame
        # stream's losses detectable: a writer that cannot name the spans it
        # touched (a reconciler, a materialize reparent) still advances this,
        # so a reader comparing it against its own tally re-asks for bodies
        # instead of trusting a cache nothing told it was stale.
        'span_epoch': projection_cache.epoch(_stored_group_ids(trace_id)),
        **structural,
        # Prompts typed while the agent is busy fire no hook; surface what's
        # currently queued (derived live from the transcript, ephemeral).
        'queued_prompts': _queued_prompts(trace_id),
        'task_list': _fetch_session_task_list(trace_id),
        'agent_roster': roster,
        **_bridge_reachability(trace_id),
        **_session_summary(trace_id, roster=roster, activity=activity),
    })


def _delegated_count_field(trace_id) -> dict:
    """`span_count_delegated`: how many of `span_count_total` a child trace
    serves instead of this view, so the two numbers name their difference
    rather than leaving it as an unexplained gap.

    Counted over the same population `_total_span_count` counts, and narrowed
    to the agents a marker names — the two conditions that make the three
    numbers reconcile. PENDING is what `agent_span_count` gets wrong on its
    own (a pre-merge figure: 4,165 against 2,708 on one measured session), and
    an agent with no marker is still rendered inline, so counting it here
    would report a row as both shown and delegated.

    Omitted when nothing was delegated, and for a subagent trace, which sends
    nothing anywhere: a session that never launched an agent answers exactly
    as it did before.
    """
    from lib.orm.engine import get_connection
    from lib.trace.alias import sql_in
    from lib.trace.pending_spans import AGENT_ID_SQL
    from lib.trace.subagent_trace import is_subagent_trace_id
    if is_subagent_trace_id(trace_id):
        return {}
    placeholders, params = sql_in(_stored_group_ids(trace_id))
    marker_ph = _in_placeholders(_AGENT_MARKER_NAMES)
    scope = f"trace_id IN {placeholders} AND name IN ({marker_ph})"
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM session_spans "
            f"WHERE trace_id IN {placeholders} AND status_code != 'PENDING' "
            f"AND name NOT IN ({marker_ph}) "
            f"AND {AGENT_ID_SQL} IN ("
            f"  SELECT {AGENT_ID_SQL} FROM session_spans WHERE {scope} "
            "   UNION SELECT json_extract(attributes, '$.launch_tool_use_id') "
            f"  FROM session_spans WHERE {scope})",
            [*params, *_AGENT_MARKER_NAMES, *params, *_AGENT_MARKER_NAMES,
             *params, *_AGENT_MARKER_NAMES]).fetchone()
    finally:
        conn.close()
    return {'span_count_delegated': row['n']} if row and row['n'] else {}


def _retired_digest(retired_span_ids) -> str:
    """Version tag for the merge's drop list.

    The list is what the client prunes from its append-only span store, and it
    is deterministic and monotonic — the same rows drop on every recompute over
    the same window. So once a client has applied a version it never needs that
    version again, and on a big session that matters: the drop list reached
    1544 ids, 37KB, HALF the whole response, re-sent every few seconds to a
    client that had already pruned every one of them.
    """
    if not retired_span_ids:
        return ''
    digest = hashlib.sha1('\n'.join(sorted(retired_span_ids)).encode())
    return digest.hexdigest()[:16]


def _shallow_structural_half(trace_id, limit, before_id, after_id) -> dict:
    """The span-derived fields of the shallow map — the expensive half.

    Split out because it is the ONLY part of the response that is a pure
    function of the stored rows, which is what lets it be memoised on an
    identifier fingerprint (`projection_cache`). Everything left in the
    caller reads a clock, a tmux pane or the transcript, and so is recomputed
    on every request even when this half is served from the memo.
    """
    from lib.orm.engine import get_connection
    from lib.trace.trace_service import projection_cache

    group = _alias_group_ids(trace_id)
    # Fingerprinted and filed under the ids the rows are WRITTEN to, which for
    # a subagent trace are its parent's: `invalidate()` names the id the writer
    # touched, so an entry filed under `sa-…` is never dropped and a live child
    # would serve its first answer forever. The served id stays in the key, so
    # parent and child keep separate entries.
    stored = _stored_group_ids(trace_id)
    conn = get_connection()
    try:
        key = ('shallow', tuple(group),
               projection_cache.fingerprint(conn, stored),
               limit, before_id, after_id)
    finally:
        conn.close()
    hit = projection_cache.get(key)
    if hit is not None:
        return hit

    widened, tree, has_more_older, retired_span_ids = trace_service.fetch_session_paginated(
        trace_id, limit=limit, before_id=before_id, after_id=after_id,
        drop_delegated=True,
    )
    by_id = {s['span_id']: s for s in widened}
    root_ids = {n['data']['span_id'] for n in tree}
    # Cursors use DB `id`: stable, monotonic, unambiguous on ties.
    ids = [n['data']['id'] for n in tree if 'id' in n.get('data', {})]
    with SessionLocal() as session:
        # span_count_total excludes PENDING placeholders (append-only keeps them).
        span_count_total = _total_span_count(session, trace_id, len(widened))
    half = {
        'spans': [s for i, s in by_id.items() if i in root_ids],
        'tree': _shallow_nodes(tree),
        'span_count': len(widened),
        'span_count_total': span_count_total,
        # `span_count_total` stays the session's true total, so it exceeds
        # what is on screen by exactly this — the rows each subagent's own
        # trace serves. Absent when nothing was delegated.
        **_delegated_count_field(trace_id),
        'has_more_older': has_more_older,
        'oldest_loaded_id': min(ids) if ids else None,
        'newest_loaded_id': max(ids) if ids else None,
        'retired_span_ids': retired_span_ids,
        'retired_digest': _retired_digest(retired_span_ids),
    }
    projection_cache.put(key, half, group)
    return half




def _queued_prompts(trace_id: str) -> list:
    from lib.trace.queued_prompts import current_queued_prompts
    dropped = _dropped_steers(trace_id)
    sdk_queue = _sdk_queue(trace_id)
    if sdk_queue is not None:
        return dropped + sdk_queue
    try:
        queued = current_queued_prompts(trace_id)
    except Exception:
        queued = []
    return dropped + queued + _pending_bridge_steers(trace_id, queued)


def _dropped_steers(trace_id: str) -> list:
    """Rows for prompts the session accepted and then abandoned.

    Served first, and never retired by an observed event: the send's ack had
    already cleared the composer, so this chip is the only copy of the text the
    operator still has. It leaves on their dismiss and on nothing else.
    """
    try:
        from lib.agent_bridge import store as bridge_store
        rows = bridge_store.list_dropped_steers(trace_id)
    except Exception:
        return []
    return [{'id': f"b{r['id']}", 'content': r['body'], 'source': 'dropped',
             'detail': r.get('delivery_detail') or '',
             'enqueued_at': r.get('created_at')} for r in rows]


def _sdk_queue(trace_id: str) -> list | None:
    """What an SDK-owned run still holds behind its current turn, or None when
    this is not such a run.

    A run regin launched queues prompts in memory, so this list IS the queue:
    an entry appears when the prompt is accepted and disappears when its turn
    starts. Neither other source can stand in for it, and both actively
    mislead here, which is why an owned run takes this path *instead of* them
    rather than merging with them:

    - the transcript path sees nothing — a launched run writes no
      `queue-operation` entries — so a queued steer would have no server
      representation at all while the prompt is still waiting.
    - the bridge path records every SDK steer as a `bridge_messages` row for
      the audit trail, but those rows are born state `closed` (this queue is
      what displays them), so the bridge chip feed rightly serves none of
      them — and its consumed-transition is transcript-derived, which a run
      with no transcript could never trigger.

    Identical prompts are deliberately not collapsed: two "continue"s are two
    queue entries and must drop one at a time. `id` is what makes that possible
    from the browser — it names one entry for the edit/remove routes, and its
    presence is also how the card knows these rows are editable at all (the
    transcript and bridge paths have no id to give, because neither queue is
    regin's to write).
    """
    from lib import agent_sdk
    from lib.agent_sdk import store as sdk_store
    try:
        if not agent_sdk.is_sdk_owned(trace_id):
            # Not live — but if regin launched it, its queue was this process's
            # and is simply gone, which is not the same as "ask the other
            # sources". The child's transcript would answer, and an edited
            # prompt would lie: it shows the body it was sent with rather than
            # the body it ran as (seen on a real run, pre-edit).
            return [] if sdk_store.find_run(trace_id) else None
        pending = agent_sdk.queued_prompts(trace_id)
    except Exception:
        return None
    return [{'id': q['id'], 'content': q['text'], 'source': 'sdk'}
            for q in pending]


def _steer_key(content) -> str:
    """Normalized body for deduping a bridge steer against the transcript
    queue — whitespace-collapsed so a composer echo and the enqueued copy
    match."""
    return ' '.join(str(content or '').split())


def _pending_bridge_steers(trace_id: str, queued: list) -> list:
    """Bridge steers still owed a chip, oldest first — and the transition
    writer that retires the rest.

    No time window: a delivered steer's row is born `pending` and stays
    chip-eligible until an observed event settles it, persisted one-way here
    so the state machine converges instead of being re-guessed every poll:

    - the transcript now represents its body (still-queued entry, or a
      processed user turn / reconstructed command line) → `consumed`; the
      transcript is authoritative from then on and the chip must never
      resurface, however the turn was logged.
    - the session ended → `closed`: a chip is a promise the pane may still
      act on the text, which an exited claude can never honour.
    - the operator removed it → `dismissed` (the DELETE route, not here).

    A steer none of those events ever touch stays visible for the session's
    lifetime — honest for a message that was delivered but never ran (a TUI
    overlay swallowed it, say), and exactly what the dismiss affordance is
    for. Bodies are matched as normalized text (`_steer_key`), so identical
    twin steers retire together once one of them runs — set-based, same
    fidelity the window-era dedup had."""
    try:
        from lib.agent_bridge import store as bridge_store
        rows = bridge_store.list_pending_steers(trace_id)
    except Exception:
        return []
    if not rows:
        return []
    if _session_has_ended(trace_id):
        bridge_store.settle_steers([r['id'] for r in rows], 'closed')
        return []
    out, settled = _partition_steers(rows, _represented_texts(trace_id, queued))
    if settled:
        bridge_store.settle_steers(settled, 'consumed')
    return out


# What the tmux tier types is not byte-identical to the body it recorded, and
# a steer chip has no time window to expire — so a retire predicate that only
# knows exact equality leaves the chip up for the session's lifetime. Two
# rewrites are known and both are reversed here rather than tolerated fuzzily:
#
#   * `attachments.for_tmux` materializes each `[Image #N]` marker into an
#     `@<path>` reference, so the transcript turn carries paths the recorded
#     body never had. The body keeps the bare markers on purpose (they bind the
#     stored bytes to the span for the thumbnail), so the PATHS come off the
#     transcript side. Matched on the `pasted/` delivery directory rather than
#     on where the path sits, because the tier has placed it both at the marker
#     and hoisted to the front — and because an operator's own `@src/foo.py`
#     must survive, or two different prompts would start retiring each other.
#   * a composer in vim NORMAL mode eats the head of the typed text as motions,
#     so the logged turn is a proper suffix of what was sent. `delivery` now
#     prevents that, but rows stranded by it still have to be able to retire.
_IMAGE_PATH_REF_RE = re.compile(
    r'@\S*/pasted/\S+?\.(?:png|jpe?g|gif|webp)\s*', re.IGNORECASE)

# Below this length a suffix match is coincidence, not a truncated echo.
_STEER_SUFFIX_FLOOR = 40


def _delivered_key(text) -> str:
    """`_steer_key` with the `@path` references the tmux tier injects removed,
    so a transcript turn and the body it was delivered from compare equal.
    Re-normalized after the strip: removing a reference closes a gap, and a
    stray double space would defeat the very comparison this exists for."""
    return ' '.join(_IMAGE_PATH_REF_RE.sub('', _steer_key(text)).split())


def _represented_texts(trace_id: str, queued: list) -> set:
    """Normalized bodies the transcript already speaks for — processed turns
    (including reconstructed command lines) plus the still-queued entries."""
    from lib.trace.queued_prompts import consumed_prompt_texts
    try:
        seen = consumed_prompt_texts(trace_id)
    except Exception:
        seen = set()
    return {_delivered_key(t) for t in seen} | {
        _delivered_key(q.get('content')) for q in queued}


def _is_represented(key: str, seen: set) -> bool:
    """True when `seen` speaks for this steer body — exactly, or across the
    two ways the head of a typed line stops matching: the composer ate it
    (the turn is a suffix of the body) or the composer already held a draft
    the text was typed onto (the body is a suffix of the turn). Either way
    the transcript demonstrably carries the steer, which is the question."""
    if key in seen:
        return True
    if len(key) < _STEER_SUFFIX_FLOOR:
        return False
    return any(len(t) >= _STEER_SUFFIX_FLOOR
               and (key.endswith(t) or t.endswith(key)) for t in seen)


def _partition_steers(rows: list, seen: set) -> tuple[list, list]:
    """Split pending steer rows into (chips to serve, row ids to settle).
    An empty body settles too: it can never dedup, so it could never leave."""
    out, settled = [], []
    for r in rows:
        key = _delivered_key(r.get('body'))
        if not key or _is_represented(key, seen):
            settled.append(r['id'])
            continue
        out.append({'id': f"b{r['id']}", 'content': r['body'],
                    'enqueued_at': r.get('delivered_at') or r.get('created_at'),
                    'source': 'bridge'})
    return out, settled


def _session_has_ended(trace_id: str) -> bool:
    """True when the session row reads closed. A steer chip is a promise the
    pane may still act on the text, which an exited claude can never honour —
    without this gate the /exit that killed a session haunts its own trace as
    a queued prompt for the rest of the delivery window. Mirrors the client's
    ended rule (useLiveTail): trust `status`; honour a stored `ended_at` only
    when no activity followed it (the ingest clears it on a genuine resume,
    but this poll can land in between). Fails open — an unreadable row must
    not hide a live session's steers."""
    from sqlmodel import select as _select
    try:
        with SessionLocal() as session:
            row = session.exec(
                _select(SessionModel.status, SessionModel.ended_at,
                        SessionModel.last_seen)
                .where(SessionModel.trace_id == trace_id)).first()
    except Exception:
        return False
    if row is None:
        return False
    status, ended_at, last_seen = row
    if status == 'ended':
        return True
    if not ended_at:
        return False
    try:
        ended = datetime.fromisoformat(str(ended_at))
        seen = datetime.fromisoformat(str(last_seen)) if last_seen else None
    except ValueError:
        return False
    return seen is None or (seen - ended).total_seconds() <= 1.0


@trace_bp.route('/api/sessions/<trace_id>/map')
def api_session_map(trace_id):
    """Return the structural map for a session.

    Every span is included, sorted chronologically. No content
    (attributes) — fetch those via /spans/<span_id>/content.

    Parent IDs are grafted server-side (orphan tool spans are re-attached
    to their owning prompt) so the frontend gets the correct tree shape
    without re-implementing the projection logic.

    Session summary (model, token counters, etc.) is included so the
    frontend can render the header without a second round-trip.

    Pass `?shallow=1` to return only root spans plus shallow tree nodes
    for lazy child expansion. Default stays backward-compatible.
    """
    if request.args.get('shallow', '').lower() in ('1', 'true'):
        return _shallow_map_response(trace_id)

    spans = _structural_map_spans(trace_id)
    summary = _session_summary(trace_id)
    return jsonify({
        'trace_id': trace_id,
        'spans': spans,
        'span_count': len(spans),
        **_delegated_count_field(trace_id),
        **summary,
    })


def _absorbed_prompt_attrs(trace_id: str, span_id: str, raw: dict) -> dict:
    """`span_id`'s prompt attributes as the serve-time merge renders them.

    A slash-command prompt stores only the bare `/command` echo on its resolved
    anchor; the text the user actually submitted lives on the PENDING
    `promptlive-` placeholder, and `_absorb_slash_command_expansions` moves it
    onto the anchor at read time. This endpoint reads ONE raw row, so without
    replaying that absorb it answers with the 22-character echo — and the
    client's content cache overlays what it returns ON TOP of the `/map`
    projection (`useSpanContentCache.allSpans`), replacing the full prompt the
    card was already rendering with the command name alone.

    Prompt rows alone are enough to replay it: the absorb pairs a prompt with a
    prompt, so the window is one small indexed read rather than the session.
    """
    from lib.trace.merge import _absorb_slash_command_expansions
    from sqlmodel import select as _select

    scope = _subagent_scope(trace_id)
    canonical = trace_id if scope is not None else _alias_group_ids(trace_id)[0]
    with SessionLocal() as session:
        rows = session.execute(
            _scoped_span_query(
                _select(
                    SessionSpan.id, SessionSpan.trace_id, SessionSpan.span_id,
                    SessionSpan.name, SessionSpan.status_code,
                    SessionSpan.attributes,
                ),
                trace_id, scope)
            .where(SessionSpan.name == 'prompt')
            .order_by(SessionSpan.id.asc())
        ).mappings().all()
    prompts = [_map_row_to_span(r, canonical) for r in rows]
    for s in _absorb_slash_command_expansions(prompts):
        if s.get('span_id') == span_id:
            return s.get('attributes') or raw
    # Unreachable for an anchor; a placeholder the absorb consumed keeps its
    # own text, which is the same text it just handed to the anchor.
    return raw


@trace_bp.route('/api/sessions/<trace_id>/spans/<span_id>/content')
def api_span_content(trace_id, span_id):
    """Return the content (attributes) for a single span.

    A subagent trace addresses rows stored under its parent, so the served id
    is not a `trace_id` to match on; every body the child's cards ask for would
    404 on the id the same response's spans were re-keyed to.
    """
    from sqlmodel import select as _select
    scope = _subagent_scope(trace_id)
    stmt = _select(SessionSpan.attributes, SessionSpan.name)
    if scope is not None:
        stmt = _scoped_span_query(stmt, trace_id, scope)
    else:
        stmt = stmt.where(SessionSpan.trace_id == trace_id)
    with SessionLocal() as session:
        row = session.execute(
            stmt.where(SessionSpan.span_id == span_id)
        ).first()
    if row is None:
        return jsonify({'error': 'span not found'}), 404
    attributes = json.loads(row[0] or '{}')
    if row[1] == 'prompt':
        attributes = _absorbed_prompt_attrs(trace_id, span_id, attributes)
    return jsonify({
        'trace_id': trace_id,
        'span_id': span_id,
        'attributes': attributes,
    })


@trace_bp.route('/api/sessions/<trace_id>/spans/<span_id>/rewind')
def api_span_rewind(trace_id, span_id):
    """Lazy before/after content for a `/rewind` marker's rolled-back files.

    The map carries only `rolled_back_count`; the file list + `@vN` refs live
    in the marker's full attributes, and the actual file copies live in
    `~/.claude/file-history/<session>/`. This route joins them on demand so a
    code-rollback diff never bloats the map payload. `session_id == trace_id`
    (regin keys sessions by the Claude Code session id, which also names the
    file-history dir)."""
    from sqlmodel import select as _select
    from lib.trace.file_history import diff_versions

    with SessionLocal() as session:
        attrs_json = session.exec(
            _select(SessionSpan.attributes)
            .where(SessionSpan.trace_id == trace_id)
            .where(SessionSpan.span_id == span_id)
        ).first()
    if attrs_json is None:
        return jsonify({'error': 'span not found'}), 404
    attrs = json.loads(attrs_json or '{}')
    if attrs.get('kind') != 'rewind':
        return jsonify({'error': 'not a rewind marker'}), 400
    files = [
        diff_versions(trace_id, entry)
        for entry in (attrs.get('rolled_back_files') or [])
    ]
    return jsonify({
        'trace_id': trace_id,
        'span_id': span_id,
        'files': files,
    })


def _workflow_run_summary(session, run_id: str) -> dict:
    """Read-time rollup for a captured workflow run (its trace_id == run_id).

    Returns the counts the parent session's collapsed summary node shows:
    agent/phase totals, the run's status, and summed agent output tokens.
    Computed from spans on every call — nothing is denormalized, so this
    stays clear of the schema.sql/Alembic drift trap.
    """
    from sqlmodel import select as _select
    from sqlalchemy import func as _func

    def _count(name: str) -> int:
        return session.exec(
            _select(_func.count())
            .select_from(SessionSpan)
            .where(SessionSpan.trace_id == run_id)
            .where(SessionSpan.name == name)
        ).one() or 0

    tokens = session.exec(
        _select(_func.sum(
            _func.json_extract(SessionSpan.attributes, '$.tokens')))
        .where(SessionSpan.trace_id == run_id)
        .where(SessionSpan.name == 'subagent.start')
    ).one()
    status = session.exec(
        _select(_func.json_extract(SessionSpan.attributes, '$.workflow_status'))
        .where(SessionSpan.trace_id == run_id)
        .where(SessionSpan.name == 'session.start')
    ).first()
    return {
        'agent_count': _count('subagent.start'),
        'phase_count': _count('workflow.phase'),
        'tokens': int(tokens) if tokens is not None else None,
        'status': status or None,
    }


@trace_bp.route('/api/sessions/<trace_id>/workflow-runs')
def api_session_workflow_runs(trace_id):
    """Dynamic-workflow runs launched from this session, in call order.

    Each ``tool.Workflow`` call this session made carries a
    ``workflow_run_id`` (+ ``workflow_name``), stamped at ingest by matching
    the persisted script to the captured run. Surfaced so the session header
    can offer a ``workflows N`` pivot chip (mirroring the ``plans`` / ``tasks``
    chips) that jumps straight to each run's captured trace. Empty for
    sessions that launched no workflows (and for a run's own session, which
    has no ``tool.Workflow`` spans).
    """
    from sqlmodel import select as _select

    with SessionLocal() as session:
        rows = session.exec(
            _select(SessionSpan.attributes)
            .where(SessionSpan.trace_id == trace_id)
            .where(SessionSpan.name == 'tool.Workflow')
            .order_by(SessionSpan.start_time)
        ).all()

        runs: list[dict] = []
        seen: set[str] = set()
        for attrs_json in rows:
            attrs = json.loads(attrs_json or '{}')
            run_id = attrs.get('workflow_run_id')
            if not run_id or run_id in seen:
                continue
            seen.add(run_id)
            # Counts are computed on read from the run's captured spans (its
            # own trace_id == run_id) — no denormalized column, so no
            # schema.sql/Alembic drift. Lets the parent session render a rich
            # collapsed summary node (agents · phases · status · tokens)
            # without opening the run.
            runs.append({
                'run_id': run_id,
                'name': attrs.get('workflow_name'),
                **_workflow_run_summary(session, run_id),
            })
    return jsonify({'trace_id': trace_id, 'items': runs})


def _delegated_child_trace(trace_id: str, span_id: str) -> str | None:
    """The trace that serves a span this session no longer does, or None when
    the span is not a subagent's.

    Resolved off the stored row rather than the rendered map, which is exactly
    where the span has stopped being: a `?span=` deep link and a search hit
    both name a row that outlived the view it was found in.

    The agent id is re-read through the marker that names the launching call,
    because an SDK run tags its half of an agent's spans with that call's id
    (`projection._normalize_agent_ids`) and the child is addressed by the
    roster id.
    """
    from lib.orm.engine import get_connection
    from lib.trace.alias import sql_in
    from lib.trace.pending_spans import AGENT_ID_SQL
    from lib.trace.subagent_trace import subagent_trace_id

    placeholders, params = sql_in(_stored_group_ids(trace_id))
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT {AGENT_ID_SQL} AS agent_id FROM session_spans "
            f"WHERE trace_id IN {placeholders} AND span_id = ? LIMIT 1",
            [*params, span_id]).fetchone()
        agent_id = row['agent_id'] if row else None
        if not agent_id:
            return None
        named = conn.execute(
            f"SELECT {AGENT_ID_SQL} AS agent_id FROM session_spans "
            f"WHERE trace_id IN {placeholders} "
            "AND json_extract(attributes, '$.launch_tool_use_id') = ? LIMIT 1",
            [*params, agent_id]).fetchone()
    finally:
        conn.close()
    return subagent_trace_id(
        (named['agent_id'] if named else None) or agent_id)


@trace_bp.route('/api/sessions/<trace_id>/spans/<span_id>/ancestors')
def api_span_ancestors(trace_id, span_id):
    """Walk parent_id up to the projected root and return the chain.

    The DB-level `parent_id` is NOT authoritative for tree shape, and
    neither is a per-row mirror of any single graft rule. The store is
    append-only, so a live `promptlive-` placeholder and its promoted
    `prompt-<uuid>` anchor coexist; the serve-time `merge_spans` drops
    the placeholder, then grafts NULL-parent orphans (rule.check, tool
    attachments) under the surviving anchor and reparents subagent spans
    by `agent_id`. Resolving the chain off raw rows lands on the retired
    placeholder (or skips the subagent nesting) — a root the frontend no
    longer renders, so the deep-link loads the wrong subtree and the jump
    silently fails.

    So compute the chain from the SAME merged span set the `/map` and
    `/children` endpoints render (`_structural_map_spans`): walk its
    post-merge `parent_id` from the target up to the root. The returned
    root is guaranteed to exist in the rendered tree, and a `children?deep`
    fetch of it surfaces the target.

    Cycle-guarded against a self-referential parent_id.
    """
    by_id = {s['span_id']: s for s in _structural_map_spans(trace_id)}
    if span_id not in by_id:
        child = _delegated_child_trace(trace_id, span_id)
        if child is None:
            return jsonify({'error': 'span not found'}), 404
        # 200 with no chain rather than 404: there is nothing to reveal in
        # this trace, but the span is reachable and the client is told where.
        return jsonify({
            'trace_id': trace_id,
            'span_id': span_id,
            'child_trace_id': child,
            'root_span_id': None,
            'chain': [],
        })

    chain: list[str] = []
    current_id = span_id
    seen: set[str] = set()
    while current_id and current_id not in seen:
        seen.add(current_id)
        span = by_id.get(current_id)
        if not span:
            # Dangling parent (merge heals these, but stay defensive). Stop.
            break
        chain.append(current_id)
        current_id = span.get('parent_id')

    chain.reverse()  # root first
    return jsonify({
        'trace_id': trace_id,
        'span_id': span_id,
        'root_span_id': chain[0],
        'chain': chain,
    })


# Every table that carries a session's id, paired with the column it lives
# under and the response key it reports. The single- and batch-delete paths
# both run this list so they can never drift (a missed table here leaks rows
# that no reader can ever reach again — they go stale forever). Keep in sync
# with db/schema.sql whenever a new trace_id/session_id-keyed table is added.
_SESSION_DELETE_TARGETS = (
    ('sessions', SessionModel, SessionModel.trace_id),
    ('spans', SessionSpan, SessionSpan.trace_id),
    ('trace_map', SessionTraceMap, SessionTraceMap.trace_id),
    ('turn_usage', TurnUsage, TurnUsage.trace_id),
    ('session_repos', SessionRepo, SessionRepo.trace_id),
    ('session_tags', SessionTag, SessionTag.trace_id),
    ('skill_reads', SkillRead, SkillRead.session_id),
    ('plan_sessions', PlanSession, PlanSession.session_id),
    ('rule_triggers', RuleTrigger, RuleTrigger.session_id),
    ('prompt_images', PromptImage, PromptImage.trace_id),
)


def _delete_session_rows(session, trace_id):
    """Delete every row keyed to `trace_id` across all session tables.

    Returns a dict of per-table rowcounts. Caller owns the transaction so
    single- and batch-delete can each wrap their own BEGIN IMMEDIATE.
    """
    from sqlalchemy import delete as _delete
    counts = {}
    for key, model, column in _SESSION_DELETE_TARGETS:
        counts[key] = session.execute(
            _delete(model).where(column == trace_id)
        ).rowcount
    return counts


@trace_bp.route('/api/sessions/batch-delete', methods=['POST'])
def api_session_batch_delete():
    """Delete multiple sessions in a single transaction.

    Body: `{"trace_ids": ["id1", "id2", ...]}`

    Wrapped in BEGIN IMMEDIATE so a crash midway through a large batch
    cannot leave some sessions partially deleted. Unknown trace_ids are
    simply no-ops (rowcount==0) instead of an error, so the caller can
    retry a batch idempotently after a timeout.
    """
    body = request.get_json(silent=True) or {}
    trace_ids = body.get('trace_ids') or []
    if not isinstance(trace_ids, list) or not all(isinstance(t, str) and t for t in trace_ids):
        return jsonify({
            'ok': False,
            'error': 'trace_ids must be a non-empty list of strings',
        }), 400
    if not trace_ids:
        return jsonify({'ok': False, 'error': 'trace_ids must not be empty'}), 400

    totals = {key: 0 for key, _, _ in _SESSION_DELETE_TARGETS}
    try:
        with SessionLocal() as session:
            # A regin-launched run is two trace ids shown as one row, so
            # deleting the row the user sees has to take both halves. Removing
            # only the canonical one left the other holding the session's
            # spans, and it surfaced as a NEW row where the deleted one had
            # been — the same session, apparently undeletable.
            for trace_id in _expand_alias_groups(trace_ids):
                for key, count in _delete_session_rows(session, trace_id).items():
                    totals[key] += count
            session.commit()
    except Exception as exc:
        return jsonify({
            'ok': False,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500
    return jsonify({
        'ok': True,
        'processed': len(trace_ids),
        'deleted': totals,
    })


@trace_bp.route('/api/sessions/<trace_id>', methods=['DELETE'])
def api_session_delete(trace_id):
    """Delete a session and all of its related trace rows.

    Every session-keyed table is cleared (see `_SESSION_DELETE_TARGETS`):
    sessions/session_spans/session_trace_map/turn_usage/prompt_images,
    session_repos and session_tags key on trace_id; skill_reads/
    plan_sessions/rule_triggers key on session_id. Everything is removed in
    one BEGIN IMMEDIATE so a
    crash mid-delete can't leave half a session in the DB (which would leak
    into the sessions list with a missing title or zero spans) or strand
    trace_map/turn_usage/repo rows that no reader can ever reach again.
    """
    try:
        with SessionLocal() as session:
            deleted = {key: 0 for key, _, _ in _SESSION_DELETE_TARGETS}
            # Both halves of a regin-launched run — see the batch endpoint.
            for member in _alias_group_ids(trace_id):
                for key, count in _delete_session_rows(session, member).items():
                    deleted[key] += count
            session.commit()
    except Exception as exc:
        return jsonify({
            'ok': False,
            'trace_id': trace_id,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500
    return jsonify({
        'ok': True,
        'trace_id': trace_id,
        'deleted': deleted,
    })


def _session_custom_tags(session, trace_id):
    """The custom tags on a session as `{slug, source}`, sorted by slug.

    Carries `source` ('manual' | 'auto') so a mutation response lets the
    frontend re-render the row's chips without relabeling auto tags as manual.
    """
    from sqlmodel import select as _select
    rows = session.exec(
        _select(SessionTag.tag, SessionTag.source)
        .where(SessionTag.trace_id == trace_id)
    ).all()
    return [{'slug': tag, 'source': source or 'manual'}
            for tag, source in sorted(rows)]


@trace_bp.route('/api/session-tags')
def api_session_tags():
    """List the custom tags in use, with how many sessions carry each.

    Powers the tag facet's custom section and add-tag autocomplete. Builtin
    category tags are NOT listed here — they come from `builtin_tags` /
    `tag_counts` on the sessions envelope (they're derived, not stored).
    """
    from sqlalchemy import func as _func
    from sqlmodel import select as _select
    with SessionLocal() as session:
        rows = session.exec(
            _select(SessionTag.tag, _func.count())
            .group_by(SessionTag.tag)
            .order_by(SessionTag.tag)
        ).all()
    return jsonify({'tags': [{'slug': t, 'count': n} for t, n in rows]})


@trace_bp.route('/api/sessions/<trace_id>/tags', methods=['POST'])
def api_session_tag_add(trace_id):
    """Add a custom tag to a session (idempotent).

    Body: `{"tag": "<slug>"}`. The slug is normalized/validated; builtin
    category slugs are rejected (they're intrinsic, derived from origin, and
    can't be hand-assigned). Returns the session's full custom tag list.
    """
    body = request.get_json(silent=True) or {}
    slug = normalize_custom_slug(body.get('tag'))
    if not slug:
        return jsonify({
            'ok': False,
            'error': 'tag must be a slug of a-z, 0-9, dash (1-40 chars) and '
                     'not a builtin category',
        }), 400
    try:
        with SessionLocal() as session:
            exists = session.get(SessionTag, (trace_id, slug))
            if exists is None:
                now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                session.add(SessionTag(trace_id=trace_id, tag=slug,
                                       source='manual', created_at=now))
                session.commit()
            tags = _session_custom_tags(session, trace_id)
    except Exception as exc:
        return jsonify({
            'ok': False,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500
    return jsonify({'ok': True, 'trace_id': trace_id, 'tags': tags})


@trace_bp.route('/api/sessions/<trace_id>/tags/<tag>', methods=['DELETE'])
def api_session_tag_remove(trace_id, tag):
    """Remove a custom tag from a session.

    Builtin category slugs can't be removed (they're derived, not stored);
    the attempt is a 400 rather than a silent no-op. Unknown (session, tag)
    pairs delete zero rows and still return ok — idempotent.
    """
    from sqlalchemy import delete as _delete
    if is_builtin(tag):
        return jsonify({
            'ok': False,
            'error': f'"{tag}" is a builtin category and cannot be removed',
        }), 400
    try:
        with SessionLocal() as session:
            session.execute(
                _delete(SessionTag)
                .where(SessionTag.trace_id == trace_id)
                .where(SessionTag.tag == tag)
            )
            session.commit()
            tags = _session_custom_tags(session, trace_id)
    except Exception as exc:
        return jsonify({
            'ok': False,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500
    return jsonify({'ok': True, 'trace_id': trace_id, 'tags': tags})


@trace_bp.route('/api/sessions/<trace_id>/close', methods=['POST'])
def api_session_close(trace_id):
    """Manually mark a session as ended.

    Some sessions crash or get interrupted before a SessionEnd span ever
    fires, so their status stays 'active' (or NULL) with no way to settle
    it from the trace itself. This records a manual close: status='ended',
    ended_reason='manual', and — when no natural end exists yet — ended_at
    set to now.

    Setting ended_at is what makes the close *stick*: the sessions upsert
    (`_SESSIONS_UPSERT_SQL` in trace_service.ingest) recomputes status on
    every re-ingested span batch from `ended_at` vs `last_start_at`, so a
    status-only write would flip back to 'active' the next time a late span
    lands. A now() ended_at is >= any past start, so the CASE resolves to
    'ended' and holds. An existing (natural) ended_at is left untouched.
    """
    try:
        with SessionLocal() as session:
            row = session.get(SessionModel, trace_id)
            if row is None:
                return jsonify({
                    'ok': False,
                    'trace_id': trace_id,
                    'error': 'session not found',
                }), 404
            row.status = 'ended'
            row.ended_reason = 'manual'
            if row.ended_at is None:
                row.ended_at = datetime.now().isoformat()
            session.commit()
            ended_at = row.ended_at
    except Exception as exc:
        return jsonify({
            'ok': False,
            'trace_id': trace_id,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500
    return jsonify({
        'ok': True,
        'trace_id': trace_id,
        'status': 'ended',
        'ended_reason': 'manual',
        'ended_at': ended_at,
    })


@trace_bp.route('/api/sessions/<trace_id>/materialize', methods=['POST'])
def api_session_materialize(trace_id):
    """Persist the orphan-graft + envelope-widen projection back to the DB.

    Wraps the fetch + projection + persist chain in BEGIN IMMEDIATE so:
    • Concurrent ingest waits for the projection to commit instead of
      slipping rows in between fetch and writes.
    • Multiple UPDATEs either all apply or none do.

    Exposed separately from GET so the read path stays safe/idempotent.
    Service: `lib.trace.trace_service.materialize_session`.
    """
    try:
        updated = trace_service.materialize_session(trace_id)
    except Exception as exc:
        return jsonify({
            'ok': False,
            'trace_id': trace_id,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500
    return jsonify({
        'ok': True,
        'trace_id': trace_id,
        'parent_updates': updated['parent_updates'],
        'envelope_updates': updated['envelope_updates'],
    })


@trace_bp.route('/api/sessions/<trace_id>/repair-spans', methods=['POST'])
def api_session_repair_spans(trace_id):
    """Recover assistant_response / assistant.thinking / harness.* spans
    that turn_trace's seen-uuid cache locked out.

    See `lib.trace.repair.repair_session_spans` for the full mechanism.
    Idempotent: re-running on a healed session is a no-op because the
    cache no longer holds any uuids without spans.
    """
    from lib.trace.repair import repair_session_spans

    try:
        result = repair_session_spans(trace_id)
    except Exception as exc:
        return jsonify({
            'ok': False,
            'trace_id': trace_id,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500
    status = 200 if result.get('ok') else 404
    return jsonify(result), status






@trace_bp.route('/api/sessions/<trace_id>/features')
def api_session_features(trace_id):
    """What this session built, read the way the feature page counts it.

    The merged reader, not `features_for_session`: that one answers what the
    session *claimed*, which is what blame demands, and on the live database
    22 of 23 attributed traces reach their feature only through the task lane.
    A header built on the narrower reader would call them unattributed while
    the feature page counted them — two surfaces disagreeing about one fact.

    It lives on the trace blueprint because it is a property of a session, and
    the trace surface's admin gate is what governs it.
    """
    from lib.features import attribution  # noqa: PLC0415  (heavy import)
    from lib.features import store as feature_store  # noqa: PLC0415
    rows = attribution.attributions_for_session(trace_id)
    # The title rides along resolved, and it is `features.title` — the
    # authority for what a feature is called. No surface falls back to the
    # title of whichever task happens to sit under it.
    titles = {f.id: f.title for f in feature_store.population()}
    return jsonify({
        "trace_id": trace_id,
        "features": [{"feature_id": row.feature_id,
                      "title": titles.get(row.feature_id),
                      "provenance": row.provenance,
                      "is_primary": bool(row.is_primary),
                      "round": row.round,
                      "task_id": row.task_id} for row in rows],
    })
