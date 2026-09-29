"""`GET /api/sessions`: the filtered, faceted, paginated session list."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from flask import request, jsonify
from lib.providers import canonical_agent_kind
from lib.trace.session_tags import (
    builtin_tag_for_origin, builtin_meta, is_builtin, normalize_custom_slug,
    origins_for_builtin, system_origins, BUILTIN_TAGS,
)
from lib.orm import SessionLocal
from lib.orm.models import (
    PlanSession, Session as SessionModel, SessionRepo, SessionSpan,
)
from lib.utils.pagination import clamp_size, keyset_page_stmt
from lib.trace.alias import aliased_run_ids
from web.blueprints.trace import trace_bp
from web.blueprints.trace.sessions.phase import _owned_run_phase


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


def _holds_work():
    """`EXISTS` clause: this session holds at least one `prompt` or `tool.*` span.

    Span truth, not `sessions.prompts` / `sessions.tool_calls`: those counters
    are only bumped by the ingest batch that inserted the span, so a prompt
    that reached the store by any other path (repair, alias regrouping) leaves
    the counter at 0 — 102 rows in the dev DB read `prompts = 0` while holding
    a real `prompt` span, and a counter-keyed filter would have hidden every
    one of them as empty.

    Correlated rather than an `IN (SELECT …)` list, and a range comparison
    rather than `LIKE 'tool.%'`: SQLite's default LIKE is case-insensitive, so
    it cannot prefix-scan, and the list subquery is then materialized by
    scanning every span once per statement — page, total, active, repo facet
    and hidden count each paid it, 39ms → 959ms on a 665k-span store. `EXISTS`
    searches `session_spans` by trace_id and stops at the first hit, which
    costs one probe per session row instead (~93ms, flat to page 10, and
    scaling with session count rather than span count). `'/'` is `'.'` + 1, so
    the range matches exactly what the LIKE did. It is also NULL-safe, which
    `NOT IN` over a nullable column is not.
    """
    from sqlmodel import select as _select
    from sqlalchemy import or_ as _or, and_ as _and
    from lib.orm.models.trace import SessionSpan
    return (_select(SessionSpan.trace_id)
            .where(SessionSpan.trace_id == SessionModel.trace_id)
            .where(_or(SessionSpan.name == 'prompt',
                       _and(SessionSpan.name >= 'tool.',
                            SessionSpan.name < 'tool/')))
            .correlate(SessionModel)
            .exists())


def _apply_empty(stmt, mode):
    """The `empty` axis: a launch that never carried work.

    Claude Code mints a session id and fires SessionStart at process launch,
    before any prompt exists, so a `claude` quit at the prompt box — or a
    `/clear` that re-ids the session — leaves a permanent 3-5 span row
    (session.start, instructions.loaded, environment.git_status, session.end)
    that regin cannot tell from a real session by origin. It is orthogonal to
    `origin` (who launched it), which is why it gets its own axis rather than
    another origin value.
    """
    if mode == 'only':
        return stmt.where(~_holds_work())
    if mode == 'hide':
        return stmt.where(_holds_work())
    return stmt


def _tri_state(value, default='show'):
    """Normalize a hide|show|only query param, falling back to `default`."""
    v = (value or default).strip().lower()
    return v if v in ('hide', 'show', 'only') else default


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


def _session_context_fields(d):
    """Derive context_pct using the session's richer `model` id (which
    carries the [1m] variant suffix when applicable) rather than the
    per-turn-span `context_window_tokens` — the handler computes that from
    the transcript's bare `message.model` and therefore can't see the 1M
    promotion. `context_pct` is the main-conversation peak
    (terminal-matching, used as headline); `context_pct_all` is the
    all-inclusive peak (rolled-in advisor / server-side sub-call tokens) — UI
    shows it as a secondary chip only when the two diverge. The headline
    divides the *live* peak (main-flow high-water mark since the last
    `/compact`) so the % drops when the session compacts; the all-time peaks
    still drive window inference and the pre-compaction hint."""
    from lib.tokens.model_windows import infer_window as _infer_window
    peak_full = d.get('peak_context_tokens')
    if not isinstance(peak_full, int):
        d['context_pct'] = None
        d['context_pct_all'] = None
        return
    win = _infer_window(d.get('model'), peak_full)
    d['context_window_tokens'] = win  # override stored hint
    main_for_pct = next(
        (v for v in (d.get('live_context_tokens'),
                     d.get('peak_main_context_tokens'), peak_full)
         if isinstance(v, int)),
        peak_full)
    d['context_pct_all'] = round(peak_full * 100.0 / win, 1) if win > 0 else None
    d['context_pct'] = round(main_for_pct * 100.0 / win, 1) if win > 0 else None


def _session_activity_fields(d):
    """Pre-compute active_pct and idle_ms so the frontend doesn't need to
    parse timestamps and do arithmetic per rendered row.

    Normalized to naive local (`parse_naive_ts`), never raw awareness: a row
    can mix a tz-aware `started_at` with a naive `last_seen` (writers
    disagree — spec-posted spans stamp `…Z`, hooks stamp local), and one such
    row's subtraction 500s the whole listing."""
    from lib.trace.pending_spans import parse_naive_ts as _parse_iso
    active_ms = d.get('active_work_ms')
    t0 = _parse_iso(d.get('started_at'))
    t1 = _parse_iso(d.get('last_seen'))
    if active_ms is None or not (t0 and t1):
        d['active_pct'] = None
        d['idle_ms'] = None
        return
    total_ms = int((t1 - t0).total_seconds() * 1000)
    if total_ms > 0:
        d['active_pct'] = round(active_ms * 100.0 / total_ms)
        d['idle_ms'] = max(0, total_ms - active_ms)
    else:
        d['active_pct'] = None
        d['idle_ms'] = 0


def _session_row_to_dict(row) -> dict:
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
    _session_context_fields(d)
    _session_activity_fields(d)

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


def _plans_subquery():
    """`plans` is computed live from the plan_sessions table rather than a
    stored counter: it counts the distinct plan files this session
    authored/edited, so it stays correct as new plans land without an
    ingest-time counter (the old `plan_enters` span counter is dead —
    `plan.enter` spans are no longer emitted). Tiny table; the correlated
    subquery is cheap."""
    from sqlalchemy import func as _func
    from sqlmodel import select as _select
    return (
        _select(_func.count(_func.distinct(PlanSession.plan_filename)))
        .where(PlanSession.session_id == SessionModel.trace_id)
        .correlate(SessionModel)
        .scalar_subquery()
        .label('plans')
    )


def _session_list_columns():
    return (
        SessionModel.trace_id, SessionModel.title, SessionModel.title_source,
        SessionModel.status, SessionModel.ended_at, SessionModel.ended_reason,
        SessionModel.started_at, SessionModel.last_seen,
        SessionModel.span_count, SessionModel.skill_reads,
        SessionModel.file_edits, SessionModel.rule_checks,
        _plans_subquery(), SessionModel.prompts,
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
    )


def _choice(name, default, allowed):
    value = (request.args.get(name) or default).strip().lower()
    return value if value in allowed else default


def _session_list_kind():
    kind = (request.args.get('kind') or '').strip().lower()
    if kind in ('real', 'test', 'all'):
        return kind
    # Fall back to legacy `include_tests`: present → 'all', absent → 'real'.
    include_tests = request.args.get('include_tests', 'false').lower() in ('1', 'true', 'yes')
    return 'all' if include_tests else 'real'


def _search_clause(search, scope):
    from sqlalchemy import func as _func, or_ as _or
    from sqlmodel import select as _select
    # COLLATE NOCASE via .ilike on SQLite; for MySQL the default
    # collation already matches case-insensitively on varchar columns.
    title_clause = SessionModel.title.ilike(f"%{search}%")
    # Substring match on the `text` attribute of `prompt` spans.
    # json_extract isolates the field so a hit on (say) a tool
    # input that happens to contain the term doesn't pollute the
    # result set. SQLite 3.38+ ships json1 by default.
    prompt_clause = SessionModel.trace_id.in_(
        _select(SessionSpan.trace_id)
        .where(SessionSpan.name == 'prompt')
        .where(
            _func.json_extract(SessionSpan.attributes, '$.text')
            .ilike(f"%{search}%")
        )
    )
    return {'title': title_clause, 'prompt': prompt_clause}.get(
        scope, _or(title_clause, prompt_clause))


_KIND_IS_TEST = {'real': 0, 'test': 1}


class _SessionListQuery:
    """The `/api/sessions` filter set, parsed once from the request so the
    page query and every count query run an identical WHERE."""

    def __init__(self):
        self.kind = _session_list_kind()
        self.active_filter = _choice('active', 'all', ('all', 'active', 'inactive'))
        self.trace_id_q = (request.args.get('trace_id') or '').strip()
        self.search = (request.args.get('q') or '').strip()
        self.scope = _choice('scope', 'title', ('title', 'prompt', 'both'))
        self.since = (request.args.get('since') or '').strip()
        self.until = (request.args.get('until') or '').strip()
        self.repo = request.args.get('repo')
        self.workflow = _tri_state(request.args.get('workflow'))
        self.empty = _tri_state(request.args.get('empty'))
        if self.trace_id_q and self.empty == 'hide':
            self.empty = 'show'
        self.tag_slugs = _parse_tag_filter()
        self.hidden_alias_ids = ()

    @property
    def date_filtered(self):
        return bool(self.since or self.until)

    def _apply_trace_id(self, s):
        if self.trace_id_q:
            return s.where(SessionModel.trace_id.ilike(f"{self.trace_id_q}%"))
        if self.hidden_alias_ids:
            # A regin-launched run is traced twice — once by the runner, once
            # by the child `claude`'s own hooks — and the reader serves both
            # ids as one merged session. Listing both would show the same run
            # twice. The alias is hidden, never the canonical row, and only
            # when the caller is browsing: an explicit `trace_id` prefix is a
            # deep link and must still resolve the run's own id, which is what
            # `/live` links carry.
            return s.where(SessionModel.trace_id.notin_(self.hidden_alias_ids))
        return s

    def _apply_dates(self, s):
        if self.since:
            s = s.where(SessionModel.last_seen >= self.since)
        if self.until:
            s = s.where(SessionModel.last_seen < self.until)
        return s

    def apply_filters(self, s, skip_date=False, skip_repo=False):
        """Apply the shared WHERE set (kind/is_test, trace_id, active,
        title/prompt search, since/until, repo) to a statement and return it.

        Shared so the page query and the workflow_hidden_count query run an
        identical filter set — the count must reflect exactly the rows the
        page would have shown but for the `workflow` origin filter. The
        workflow/origin clause is applied by the caller, NOT here.
        `skip_date=True` omits the since/until bounds so callers can count
        rows outside the current date window; `skip_repo=True` omits the repo
        narrowing so a facet can still show its sibling repos' counts.
        """
        if self.kind in _KIND_IS_TEST:
            s = s.where(SessionModel.is_test == _KIND_IS_TEST[self.kind])
        s = self._apply_trace_id(s)
        if self.active_filter != 'all':
            s = s.where(_session_active_clause(self.active_filter == 'active'))
        if self.search:
            s = s.where(_search_clause(self.search, self.scope))
        if not skip_date:
            s = self._apply_dates(s)
        if skip_repo:
            return s
        return _filter_sessions_by_repo(s, self.repo)

    def apply_origin(self, s):
        """The `workflow` (origin) axis, applied AFTER the shared filters.

        NULL legacy rows count as 'session' (not a run), so `hide` keeps them
        and `only` drops them. 'llm-stage' rows (regin-spawned LLM stages,
        e.g. reflect's judges) ride the same toggle as workflow captures —
        both are non-interactive runs.
        """
        from sqlalchemy import or_ as _or
        if self.workflow == 'only':
            return s.where(SessionModel.origin.in_(_RUN_ORIGINS))
        if self.workflow == 'hide':
            return s.where(_or(SessionModel.origin.is_(None),
                               SessionModel.origin.not_in(_RUN_ORIGINS)))
        return s

    def narrow(self, s, skip_repo=False):
        """The request's COMPLETE filter set: shared WHERE + origin + work + tag."""
        return _apply_tag_filter(
            _apply_empty(self.apply_origin(self.apply_filters(s, skip_repo=skip_repo)),
                         self.empty),
            self.tag_slugs)


def _count_stmt():
    from sqlalchemy import func as _func
    from sqlmodel import select as _select
    return _select(_func.count()).select_from(SessionModel)


def _hidden_workflow_count(session, q):
    """When hiding runs, count how many the SAME other filters would have
    matched so the frontend can offer a pivot hint. Also narrowed by the work
    axis: a run row that is ALSO an empty launch stays hidden when runs are
    toggled on, so counting it here would promise one more row than the
    toggle reveals."""
    if q.workflow != 'hide':
        return None
    return session.exec(_apply_empty(
        q.apply_filters(_count_stmt())
        .where(SessionModel.origin.in_(_RUN_ORIGINS)), q.empty)
    ).one()


def _empty_hidden_count(session, q):
    """Rows the SAME other filters (including the origin axis) would have
    matched but for the work axis — the chip's "N empty launches hidden"."""
    if q.empty != 'hide':
        return None
    return session.exec(_apply_tag_filter(
        _apply_empty(q.apply_origin(q.apply_filters(_count_stmt())), 'only'),
        q.tag_slugs)).one()


def _workflow_date_hidden_count(session, q):
    """When showing only runs and a date filter is active, count how many
    workflow rows the date filter excluded, for a "widen date range" hint."""
    if not (q.workflow == 'only' and q.date_filtered):
        return None
    wf_all = session.exec(
        q.apply_filters(_count_stmt(), skip_date=True)
        .where(SessionModel.origin.in_(_RUN_ORIGINS))
    ).one()
    wf_dated = session.exec(
        q.apply_filters(_count_stmt())
        .where(SessionModel.origin.in_(_RUN_ORIGINS))
    ).one()
    return max(0, wf_all - wf_dated)


def _session_list_envelope_extras(session, q):
    extras = {
        'workflow_hidden_count': _hidden_workflow_count(session, q),
        'empty_hidden_count': _empty_hidden_count(session, q),
        'workflow_date_hidden_count': _workflow_date_hidden_count(session, q),
        # Per-tag counts for the facet, over the shared filters but NOT the
        # tag selection itself (so a selected facet still shows sibling counts).
        'tag_counts': _tag_counts(session, q.apply_filters),
    }
    # Envelope facets: total/active over the SAME population the page
    # paginates, and repo counts over everything but the repo selection
    # (same sibling-counts rule as tag_counts).
    facets = _list_facet_counts(session, q.narrow)
    facets['repo_counts'] = _repo_counts(
        session, lambda s: q.narrow(s, skip_repo=True))
    return extras, facets


def _stamp_server_now(envelope):
    # One server-clock instant in BOTH stored-timestamp formats (naive
    # host-local and UTC-Z), so the frontend can age each row server−server
    # against the anchor matching that row's format. Client−server arithmetic
    # leaks the host↔viewer timezone offset: a viewer behind the host reads
    # every recent naive stamp as future ("just now").
    now = datetime.now(timezone.utc)
    envelope['server_now'] = (
        now.astimezone().replace(tzinfo=None).isoformat(timespec='microseconds'))
    envelope['server_now_utc'] = now.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


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
      empty=hide|show|only
                         — filter by the `work` axis, orthogonal to `origin`:
                           a session holds work when it has at least one
                           `prompt` or `tool.*` span. show (server default) →
                           every row; hide → only sessions that carried work;
                           only → just the launches that never did (Claude Code
                           mints a session id at process launch, so a `claude`
                           abandoned at the prompt box leaves a permanent row).
                           When 'hide' the envelope carries `empty_hidden_count`.
                           Suppressed under an explicit `trace_id=` prefix,
                           which is a deep link and must still resolve.
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
    q = _SessionListQuery()
    cursor_token = request.args.get('cursor')
    size = clamp_size(request.args.get('size'), default=50)

    with SessionLocal() as session:
        q.hidden_alias_ids = aliased_run_ids(session)
        stmt = q.narrow(q.apply_filters(_select(*_session_list_columns())))
        extras, facets = _session_list_envelope_extras(session, q)
        page = keyset_page_stmt(
            session, stmt,
            order_cols=[(SessionModel.last_seen, 'DESC'),
                        (SessionModel.trace_id, 'DESC')],
            cursor_token=cursor_token, size=size,
            row_to_dict=_session_row_to_dict,
        )
        _attach_session_repos(session, page.items)
        _attach_session_tags(session, page.items)
    envelope = page.to_envelope()
    envelope['sessions'] = envelope['items']  # legacy field
    envelope['search'] = q.search
    envelope.update(extras)
    envelope['builtin_tags'] = builtin_meta()
    envelope.update(facets)
    _stamp_server_now(envelope)
    return jsonify(envelope)
