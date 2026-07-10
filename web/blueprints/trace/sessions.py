"""Trace endpoints — split by URL grouping (skill-reads, mcp-calls, etc.)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from flask import request, jsonify, Response

from lib import hook_plugin as _hp
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
from web.helpers import (
    _is_non_blank_str, _is_iso_timestamp, _normalize_is_test,
    _ingest_max_batch_size, _ingest_max_attributes_bytes,
    _IS_TEST_WHERE, _IS_TEST_CASE,
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
    from sqlalchemy import func as _func, or_ as _or, and_ as _and
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
        from datetime import datetime as _dt

        def _parse_iso(s):
            if not s:
                return None
            try:
                return _dt.fromisoformat(s.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                return None

        d = dict(row._mapping)
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

    def _apply_filters(s, skip_date=False):
        """Apply the shared WHERE set (kind/is_test, trace_id, active,
        title/prompt search, since/until, repo) to a statement and return it.

        Extracted so the page query and the workflow_hidden_count query run
        an identical filter set — the count must reflect exactly the rows the
        page would have shown but for the `workflow` origin filter. The
        workflow/origin clause is applied by the caller, NOT here.
        `skip_date=True` omits the since/until bounds so callers can count
        rows outside the current date window.
        """
        if kind == 'real':
            s = s.where(SessionModel.is_test == 0)
        elif kind == 'test':
            s = s.where(SessionModel.is_test == 1)
        if trace_id_q:
            s = s.where(SessionModel.trace_id.ilike(f"{trace_id_q}%"))
        if active_filter != 'all':
            # Mirror the frontend's `isActive(s)` rule so the server-side
            # filter matches the green badge: status='active' is always
            # active; 'ended' is always inactive; anything else falls back
            # to the last-seen recency check.
            cutoff_iso = (datetime.now() - timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M:%S')
            status_col = SessionModel.status
            recent_unknown = _and(
                _or(status_col.is_(None), status_col.notin_(['active', 'ended'])),
                SessionModel.last_seen >= cutoff_iso,
            )
            stale_unknown = _and(
                _or(status_col.is_(None), status_col.notin_(['active', 'ended'])),
                SessionModel.last_seen < cutoff_iso,
            )
            active_clause = (_or(status_col == 'active', recent_unknown)
                             if active_filter == 'active'
                             else _or(status_col == 'ended', stale_unknown))
            s = s.where(active_clause)
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
        return _filter_sessions_by_repo(s, request.args.get('repo'))

    with SessionLocal() as session:
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
        ))
        # Apply the `workflow` (origin) filter AFTER the shared filters.
        # NULL legacy rows count as 'session' (not a run), so `hide` keeps
        # them and `only` drops them. 'llm-stage' rows (regin-spawned LLM
        # stages, e.g. reflect's judges) ride the same toggle as workflow
        # captures — both are non-interactive runs.
        if workflow == 'only':
            stmt = stmt.where(SessionModel.origin.in_(_RUN_ORIGINS))
        elif workflow == 'hide':
            stmt = stmt.where(_or(SessionModel.origin.is_(None),
                                  SessionModel.origin.not_in(_RUN_ORIGINS)))

        # Tag facet: AND every selected tag (builtin category → origin,
        # custom → session_tags membership). Orthogonal to `workflow`.
        stmt = _apply_tag_filter(stmt, tag_slugs)

        # Per-tag counts for the facet, over the shared filters but NOT the
        # tag selection itself (so a selected facet still shows sibling counts).
        tag_counts = _tag_counts(session, _apply_filters)

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
    return jsonify(envelope)


def _shallow_nodes(nodes):
    """Return a shallow copy of `nodes` with `children` stripped.
    Each node is flagged with `leaf: false` when it has descendants so the
    frontend knows to show an expander and fetch children on demand.
    The heavy `data.spans` payload (inlined direct children from the tree
    builder) is replaced with a `child_count` scalar to keep the initial
    response small."""
    out = []
    for node in nodes:
        has_children = bool(node.get('children'))
        data = dict(node['data'])
        inline_children = data.pop('spans', None)
        if has_children:
            data['child_count'] = len(inline_children) if inline_children else len(node['children'])
        out.append({
            'key': node['key'],
            'data': data,
            'leaf': not has_children,
        })
    return out


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
    widened, tree = trace_service.fetch_session_projection(trace_id)
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
    subject?, status?}`; absent fields mean "this span didn't touch
    that field." `final` is the snapshot after the last event,
    pre-sorted by numeric task_id.
    """
    from sqlmodel import select as _select
    with SessionLocal() as session:
        rows = session.exec(
            _select(
                SessionSpan.span_id, SessionSpan.name,
                SessionSpan.start_time, SessionSpan.attributes,
            )
            .where(SessionSpan.trace_id == trace_id)
            .where(SessionSpan.name.in_(('tool.TaskCreate', 'tool.TaskUpdate')))
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
    for r in rows:
        _apply_task_row(r, events, state, last_span_by_status)
    if not events:
        return None
    final = _finalize_task_state(state, last_span_by_status)
    return {'events': events, 'final': final}


def _str_attr(value) -> str:
    """Coerce an attribute to a non-empty string, or '' otherwise."""
    return value if isinstance(value, str) and value else ''


def _apply_task_row(r, events, state, last_span_by_status) -> None:
    """Fold one TaskCreate/TaskUpdate span into the event log + state.

    Appends a per-span event (omitting subject/status when absent) and
    updates the running per-task entry: first non-empty subject wins,
    every set status both overwrites the entry and records the span_id
    under that status in `last_span_by_status`.
    """
    try:
        attrs = json.loads(r.attributes) if r.attributes else {}
    except (ValueError, TypeError):
        attrs = {}
    tid = attrs.get('task_id')
    if tid is None:
        return
    tid = str(tid)
    subject = _str_attr(attrs.get('subject'))
    status = _str_attr(attrs.get('status'))
    active_form = _str_attr(attrs.get('active_form'))
    event: dict = {
        'span_id': r.span_id,
        'timestamp': r.start_time,
        'task_id': tid,
    }
    if subject:
        event['subject'] = subject
    if status:
        event['status'] = status
    events.append(event)
    entry = state.setdefault(tid, {
        'task_id': tid,
        'subject': '',
        'status': 'pending',
        'active_form': '',
        # First TaskCreate's span_id — fallback for pending tasks.
        'created_span_id': r.span_id,
    })
    if subject and not entry['subject']:
        entry['subject'] = subject
    if active_form and not entry['active_form']:
        entry['active_form'] = active_form
    if status:
        entry['status'] = status
        last_span_by_status.setdefault(tid, {})[status] = r.span_id


def _finalize_task_state(state, last_span_by_status) -> list[dict]:
    """Resolve `current_span_id` per task and return the sorted snapshot.

    `current_span_id` is the latest span that set the FINAL status,
    falling back to the TaskCreate for pending tasks. Numeric task_ids
    sort numerically; non-digit ids sink to the end then sort lexically.
    """
    for tid, entry in state.items():
        per_status = last_span_by_status.get(tid, {})
        entry['current_span_id'] = (
            per_status.get(entry['status']) or entry['created_span_id']
        )
    return sorted(state.values(), key=lambda t: (
        int(t['task_id']) if t['task_id'].isdigit() else 1_000_000,
        t['task_id'],
    ))


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
    span_count)."""
    from lib.orm.engine import get_connection

    scope_ph = _in_placeholders(_AGENT_SCOPE_MARKER_NAMES)
    marker_ph = _in_placeholders(_AGENT_MARKER_NAMES)
    conn = get_connection()
    try:
        act = conn.execute(
            f"SELECT {AGENT_ID_SQL} AS aid, "
            "       MIN(start_time) AS first_ts, MAX(start_time) AS last_ts, "
            f"      SUM(CASE WHEN name IN ({scope_ph}) OR status_code = 'PENDING' "
            "                THEN 0 ELSE 1 END) AS internal_count, "
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
    finally:
        conn.close()
    parsed_launches = [
        {'span_id': r['span_id'], 'status_code': r['status_code'],
         'ts': _roster_ts(r['start_time']), 'end': r['end_time'],
         'attrs': _row_attrs(r['attributes'])}
        for r in launches
    ]
    ended = bool(sess and sess['status'] == 'ended')
    return act, marks, parsed_launches, ended


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


def _ambiguous_launch_types(launches) -> set:
    """agent_types whose implicit-stop attribution is unsafe: any type with a
    `tool.Agent` launch that has NOT cleanly resolved (still PENDING, or
    ERROR/denied). Only when EVERY same-type launch resolved is the launch↔agent
    mapping unambiguous — see `_apply_implicit_stops`."""
    resolved_by_type: dict = {}
    for r in launches:
        typ = _launch_type(r['attrs'])
        if not typ:
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
    return {
        'start_span_id': latest['span_id'] if latest else None,
        'description': attrs.get('description') or attrs.get('label') or '',
        'prompt_preview': attrs.get('prompt_preview') or '',
        'marker_seen': (stop or latest or {}).get('start_time'),
    }


def _roster_entry_base(aid, seg_entry, activity, waiting) -> dict:
    """Segment + activity facts for one agent, before status/description."""
    latest, stop = _roster_latest_segment(
        seg_entry or {'starts': [], 'stops': []})
    act = activity.get(aid) or {}
    facts = _entry_marker_facts(latest, stop)
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
        'last_seen': last_seen,
        'span_count': int(act.get('internal_count') or 0),
        'description': facts['description'],
        'prompt_preview': facts['prompt_preview'],
        'waiting': bool(ask_ts and seen_ts and ask_ts >= seen_ts),
    }


def _roster_status(entry, interrupted_ids, now, ended) -> str:
    if entry['stop']:
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
    duration_ms = None
    if stop_ts and e['start_ts']:
        duration_ms = max(0, int((stop_ts - e['start_ts']).total_seconds() * 1000))
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
    return _build_roster(act_rows, marks, launches, ended), activity, ended


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
_PHASE_PRECEDENCE = (
    'waiting-permission', 'waiting-input', 'working',
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
    """Main-agent phase. A blocker only counts while the session is active —
    an inactive/ended session's stuck pending is demoted (merge.py), so it is
    never 'waiting'."""
    last = _roster_ts(act.get('last_ts')) if act else None
    dt = (now - last).total_seconds() if last else None
    inactive = ended or (dt is not None and dt > INACTIVE_THRESHOLD_SEC)
    kind = _waiting_kind(act) if act else None
    if kind and not inactive:
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


def _session_phase(by_aid: dict, roster, ended: bool) -> tuple:
    """(rollup phase, agent_phase map). Main from its own activity; each
    subagent from its roster status. `by_aid` is the shared per-agent activity
    map (main under None) so no third scan is needed.

    Rollup = highest-precedence phase across all agents, EXCEPT that an ended
    session always rolls up to 'ended': once the session itself ended nothing
    can be genuinely waiting, so a ghost unstopped subagent (rolled up as
    inactive-stale) must not mask the 'ended' verdict."""
    now = datetime.now()
    agent_phase = {'main': _main_phase(by_aid.get(None), now, ended)}
    for r in roster:
        aid = r['agent_id']
        agent_phase[aid] = _phase_from_roster(
            r['status'], _waiting_kind(by_aid.get(aid) or {}))
    if agent_phase['main'] == 'ended':
        return 'ended', agent_phase
    return _phase_rollup(agent_phase.values()) or agent_phase['main'], agent_phase


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
            ).where(SessionModel.trace_id == trace_id)
        ).first()
    if not row:
        return {}
    (model, cwd, input_tokens, output_tokens,
     cache_read, cache_creation, peak, peak_main, live, active_work_ms,
     started_at, ended_at, last_seen, status, ended_reason,
     title, title_source) = row
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
    phase, agent_phase = _session_phase(activity, roster, status == 'ended')
    return {
        'model': model,
        # Server phase verdict — the /live card selects on this, never
        # re-derives it. `phase` = session rollup; `agent_phase` = "main"
        # + each subagent id → its phase.
        'phase': phase,
        'agent_phase': agent_phase,
        'repo': _session_repo_name(trace_id, cwd),
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
        'title': title,
        'title_source': title_source,
    }


@trace_bp.route('/api/sessions/<trace_id>/spans/<span_id>/children')
def api_session_span_children(trace_id, span_id):
    """Return the children of `span_id`.

    By default (lazy-expand flow) returns direct children only as shallow
    tree nodes. Pass `?deep=1` to get the full subtree with inline
    children — used by the Trace view's reload path to refresh the
    active prompt's subtree without re-fetching the whole session.
    """
    deep = request.args.get('deep', '').lower() in ('1', 'true')
    widened, tree = trace_service.fetch_session_projection(trace_id)

    def find_children(nodes):
        for n in nodes:
            if n['data']['span_id'] == span_id:
                return n.get('children', [])
            found = find_children(n.get('children', []))
            if found is not None:
                return found
        return None

    children = find_children(tree) or []
    if deep:
        descendant_ids = set()
        def collect(nodes):
            for n in nodes:
                descendant_ids.add(n['data']['span_id'])
                collect(n.get('children', []))
        collect(children)
        return jsonify({
            'trace_id': trace_id,
            'parent_span_id': span_id,
            'children': children,
            'spans': [s for s in widened if s['span_id'] in descendant_ids],
        })
    child_ids = {n['data']['span_id'] for n in children}
    return jsonify({
        'trace_id': trace_id,
        'parent_span_id': span_id,
        'children': _shallow_nodes(children),
        'spans': [s for s in widened if s['span_id'] in child_ids],
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
    'agent_id',
    # ScheduleWakeup label needs these to distinguish "agent finished" (stop)
    # from "paused to resume" — else the row degrades to a bare tool name on a
    # fresh (non-shallow /map) reload. `resume_action`/`poll_*` are the
    # serve-time cross-turn link (wakeup_links.annotate_wakeup_resumes).
    'stop', 'delay_seconds', 'reason',
    'resume_action', 'resume_span_id', 'poll_round', 'poll_total',
)


def _kept_map_attrs(attrs) -> dict:
    """The small subset of a span's attributes the structural map keeps."""
    if not isinstance(attrs, dict):
        return {}
    return {k: attrs[k] for k in _MAP_KEEP_ATTR_KEYS if k in attrs}


def _structural_map_spans(trace_id: str) -> list[dict]:
    """The full structural span list for the non-shallow `/map` (Terminal tab).

    The store is append-only, so a placeholder and its real anchor coexist —
    `merge_spans` selects the winner. It correlates by prompt-text hash /
    tool_name, so it needs `attributes` (and `turn_uuid` for the reparent
    ladder); read them from session_spans rather than the attribute-less
    session_trace_map. After merging we STRIP attributes/turn_uuid so the map
    stays the light, structure-only payload the frontend expects (descendants
    fetch content lazily via /spans/<id>/content)."""
    from sqlmodel import select as _select
    from lib.trace.merge import merge_spans
    from lib.trace.trace_service.queries import _attach_prompt_expansions

    with SessionLocal() as session:
        rows = session.execute(
            _select(
                SessionSpan.id, SessionSpan.trace_id,
                SessionSpan.span_id, SessionSpan.parent_id,
                SessionSpan.name, SessionSpan.kind,
                SessionSpan.start_time, SessionSpan.end_time,
                SessionSpan.duration_ms, SessionSpan.status_code,
                SessionSpan.status_message, SessionSpan.attributes,
                SessionSpan.turn_uuid, SessionSpan.source,
            )
            .where(SessionSpan.trace_id == trace_id)
            .order_by(SessionSpan.start_time.asc(), SessionSpan.id.asc())
        ).mappings().all()
        act_row = session.exec(
            _select(SessionModel.status, SessionModel.last_seen)
            .where(SessionModel.trace_id == trace_id)
        ).first()
    session_activity = (
        {'status': act_row[0], 'last_seen': act_row[1]} if act_row else None)
    spans = []
    for r in rows:
        d = dict(r)
        try:
            d['attributes'] = json.loads(d['attributes']) if d.get('attributes') else {}
        except (TypeError, ValueError):
            d['attributes'] = {}
        spans.append(d)
    grafted = merge_spans(spans, session_activity=session_activity)
    _attach_prompt_expansions(trace_id, grafted)
    from lib.trace.wakeup_links import annotate_wakeup_resumes
    annotate_wakeup_resumes(grafted)
    for s in grafted:
        kept = _kept_map_attrs(s.get('attributes'))
        s.pop('attributes', None)
        s.pop('turn_uuid', None)
        if kept:
            s['attributes'] = kept
    return grafted


def _bridge_reachability(trace_id: str) -> dict:
    """Per-session agent-bridge availability for the /live composer.

    Rides the existing shallow-map poll so the client needs no extra
    polling loop. Exposes only a boolean + the pane label — never the
    bridge bearer token (that credential must not reach the browser).
    """
    from lib.settings import settings
    if not settings.agent_bridge.enabled:
        return {'bridge_reachable': False, 'bridge_pane': None}
    from lib.agent_bridge import store as bridge_store
    pane = bridge_store.get_reachable_pane(trace_id)
    return {'bridge_reachable': pane is not None,
            'bridge_pane': pane['pane_id'] if pane else None}


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

    widened, tree, has_more_older, retired_span_ids = trace_service.fetch_session_paginated(
        trace_id, limit=limit, before_id=before_id, after_id=after_id,
    )
    roster, activity, _ended = _roster_with_activity(trace_id)
    root_ids = {n['data']['span_id'] for n in tree}
    # Cursors use DB `id`: stable, monotonic, unambiguous on ties.
    ids = [n['data']['id'] for n in tree if 'id' in n.get('data', {})]
    oldest_loaded_id = min(ids) if ids else None
    newest_loaded_id = max(ids) if ids else None
    with SessionLocal() as session:
        # span_count_total excludes PENDING placeholders (append-only keeps them).
        span_count_total = session.exec(
            _select(_func.count())
            .select_from(SessionSpan)
            .where(SessionSpan.trace_id == trace_id)
            .where(SessionSpan.status_code != 'PENDING')
        ).one()
    return jsonify({
        'trace_id': trace_id,
        'spans': [s for s in widened if s['span_id'] in root_ids],
        'tree': _shallow_nodes(tree),
        'span_count': len(widened),
        'span_count_total': span_count_total,
        'has_more_older': has_more_older,
        'oldest_loaded_id': oldest_loaded_id,
        'newest_loaded_id': newest_loaded_id,
        'retired_span_ids': retired_span_ids,
        # Prompts typed while the agent is busy fire no hook; surface what's
        # currently queued (derived live from the transcript, ephemeral).
        'queued_prompts': _queued_prompts(trace_id),
        'task_list': _fetch_session_task_list(trace_id),
        'agent_roster': roster,
        **_bridge_reachability(trace_id),
        **_session_summary(trace_id, roster=roster, activity=activity),
    })


# A bridge steer is typed into the same tmux composer a human types into, so
# once Claude Code enqueues it, it becomes a `queue-operation` the transcript
# path already captures. This window only covers the brief gap between the
# bridge's send + Claude flushing the queue-op (or the steer being submitted
# immediately as a real prompt) — after it, the transcript is authoritative.
_BRIDGE_STEER_WINDOW_SEC = 90


def _queued_prompts(trace_id: str) -> list:
    from lib.trace.queued_prompts import current_queued_prompts
    try:
        queued = current_queued_prompts(trace_id)
    except Exception:
        queued = []
    return _merge_bridge_steers(trace_id, queued)


def _steer_key(content) -> str:
    """Normalized body for deduping a bridge steer against the transcript
    queue — whitespace-collapsed so a composer echo and the enqueued copy
    match."""
    return ' '.join(str(content or '').split())


def _recent_bridge_steers(trace_id: str) -> list:
    """Bridge steers delivered into this session's pane within the window,
    newest first. Empty when the bridge is off or the registry is absent."""
    from lib.settings import settings
    if not settings.agent_bridge.enabled:
        return []
    try:
        from lib.agent_bridge import store as bridge_store
        rows = bridge_store.list_bridge_messages(trace_id, limit=20)
    except Exception:
        return []
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now_utc - timedelta(seconds=_BRIDGE_STEER_WINDOW_SEC)
    out = []
    for r in rows:
        if not r.get('delivered'):
            continue
        delivered_at = _parse_bridge_ts(r.get('delivered_at'))
        if delivered_at is None or delivered_at < cutoff:
            continue
        body = r.get('body')
        if isinstance(body, str) and body.strip():
            out.append({'content': body, 'delivered_at': r.get('delivered_at')})
    return out


def _parse_bridge_ts(value) -> datetime | None:
    """`bridge_messages.delivered_at` is SQLite `datetime('now')` (naive UTC,
    'YYYY-MM-DD HH:MM:SS'). Compared against `datetime.utcnow()`."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('T', ' ').split('.')[0])
    except ValueError:
        return None


def _merge_bridge_steers(trace_id: str, queued: list) -> list:
    """Append recently-delivered bridge steers not already represented in the
    transcript-derived queue (deduped by normalized body). The transcript copy
    wins when both exist — a steer surfaces optimistically only until Claude
    Code records it as a queue-op or a real prompt."""
    steers = _recent_bridge_steers(trace_id)
    if not steers:
        return queued
    seen = {_steer_key(q.get('content')) for q in queued}
    for s in steers:
        key = _steer_key(s.get('content'))
        if key and key not in seen:
            seen.add(key)
            queued.append({
                'content': s['content'],
                'enqueued_at': s.get('delivered_at'),
                'source': 'bridge',
            })
    return queued


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
        **summary,
    })


@trace_bp.route('/api/sessions/<trace_id>/spans/<span_id>/content')
def api_span_content(trace_id, span_id):
    """Return the content (attributes) for a single span."""
    from sqlmodel import select as _select
    with SessionLocal() as session:
        attrs_json = session.exec(
            _select(SessionSpan.attributes)
            .where(SessionSpan.trace_id == trace_id)
            .where(SessionSpan.span_id == span_id)
        ).first()
    if attrs_json is None:
        return jsonify({'error': 'span not found'}), 404
    return jsonify({
        'trace_id': trace_id,
        'span_id': span_id,
        'attributes': json.loads(attrs_json or '{}'),
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
        return jsonify({'error': 'span not found'}), 404

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
            for trace_id in trace_ids:
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
            deleted = _delete_session_rows(session, trace_id)
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




