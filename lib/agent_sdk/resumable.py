"""Which sessions the launch sheet may offer to continue.

Resume has two shapes, and the picker has to distinguish them because they
answer different questions for the operator (`web.blueprints.agent_runs.
_resume_target` is the authority on both):

* a run **regin launched** and has a row for — continued as *itself*, under the
  run's own trace, so the conversation stays one session; and
* a session the user drove **in a terminal** — reopened by id under a fresh
  trace, because regin has no run to revive.

Candidates come from the trace store, but the trace store is not the authority
on whether the CLI can actually reopen one: regin records a session the moment
a hook fires for it, while `claude --resume` needs the provider's own transcript
still on disk. So every candidate is confirmed against
`find_session_transcript` before it is offered — a list that includes ids the
CLI will reject just moves the failure from the picker to a run that dies on
start, where it costs the operator a launch to discover.

Nothing here launches anything; it only answers *what could be launched*.
"""

from __future__ import annotations

import functools

# The `sdk-<hex>` half of a launched run is never a resume target: the CLI has
# no session under it (see `lib/trace/alias.py` — the child's id is canonical,
# and it is the one `--resume` understands).
_SYNTHETIC_PREFIX = 'sdk-'

# Rows to consider before the on-disk confirmation, per page requested. The
# transcript check is a filesystem glob per candidate, so it cannot run over the
# whole table; over-fetching absorbs the sessions whose transcript has since
# been cleaned without making the caller paginate through them. The `q` filter
# runs in SQL *before* this cap, so searching still reaches old sessions.
_OVERFETCH = 4
_OVERFETCH_MAX = 400


def _candidate_rows(query: str, limit: int) -> list:
    """Newest sessions matching `query`, before the resumability filters.

    Matching spans title, id and cwd because an operator looking for a past run
    remembers one of the three, and which one is not predictable — a repo path
    for "that thing I did in the worktree", a title otherwise. `contains` with
    `autoescape` so a `%` or `_` typed into the search box is a literal.
    """
    from sqlmodel import col, or_, select

    from lib.orm import SessionLocal
    from lib.orm.models.trace import Session as SessionModel

    stmt = (select(SessionModel)
            .where(SessionModel.is_test == 0)
            .where(col(SessionModel.trace_id).notlike(f'{_SYNTHETIC_PREFIX}%'))
            .order_by(col(SessionModel.last_seen).desc())
            .limit(min(limit * _OVERFETCH, _OVERFETCH_MAX)))
    if query:
        stmt = stmt.where(or_(
            col(SessionModel.title).contains(query, autoescape=True),
            col(SessionModel.trace_id).contains(query, autoescape=True),
            col(SessionModel.cwd).contains(query, autoescape=True),
        ))
    with SessionLocal() as session:
        return list(session.exec(stmt).all())


def _runs_by_child(child_ids: list[str]) -> dict[str, tuple[str, str]]:
    """`{cli_session_id: (run trace_id, model)}` for the candidates regin
    launched.

    Batched rather than one `store.find_run` per row: the picker is a list, and
    a per-row lookup would put a query per candidate on every keystroke.
    """
    from sqlalchemy.exc import OperationalError
    from sqlmodel import col, select

    from lib.orm import SessionLocal
    from lib.orm.models.agent_runs import AgentRun, AgentRunSession

    if not child_ids:
        return {}
    try:
        with SessionLocal() as session:
            rows = session.exec(
                select(AgentRunSession.cli_session_id, AgentRun.trace_id,
                       AgentRun.model)
                .join(AgentRun,
                      col(AgentRun.trace_id) == AgentRunSession.trace_id)
                .where(col(AgentRunSession.cli_session_id).in_(child_ids))).all()
    except OperationalError:
        # A DB predating the alias-group migration: every candidate is
        # then a plain session, which is what it looked like before the column
        # existed. Degrading beats refusing to list anything.
        return {}
    return {child: (trace, model or '') for child, trace, model in rows if child}


def _held_by_nothing() -> frozenset[str] | None:
    return None


# Why a candidate was refused. Only `CLI_HELD` reaches a human: it is the one
# refusal that describes a session still doing something, so a page with no
# composer and no Resume button can say what it is waiting on instead of
# rendering an empty rail. The rest are ordinary absences.
REFUSED_CLI_HELD = 'cli_held'


def _refusal(session_id: str, run_trace: str | None, provider,
             held_ids=_held_by_nothing) -> str:
    """Why this candidate cannot be offered, or '' when it can."""
    from lib import agent_sdk
    from lib.agent_bridge import delivery

    if run_trace and (agent_sdk.is_sdk_owned(run_trace)
                      or agent_sdk.is_starting(run_trace)):
        return 'run_live'
    if not provider.find_session_transcript(session_id):
        return 'no_transcript'
    if delivery.session_is_live(session_id):
        return 'in_pane'
    ids = held_ids()
    if ids is not None and session_id in ids:
        return REFUSED_CLI_HELD
    return ''


def _offerable(session_id: str, run_trace: str | None, provider,
               held_ids=_held_by_nothing) -> bool:
    """Whether this candidate would survive the launch route's own checks.

    A run still live under this process is refused there ("stop it before
    resuming"), so listing it would offer an option that cannot be taken. A
    session live in a *terminal* is refused for the same reason and reads the
    same way to an operator, but liveness is a different fact about it: regin
    holds no run for a session it never launched.

    Two sources answer that, and the pane registry is the weaker one: it knows
    only which session owns a tmux pane, so a session the CLI hosts off-pane
    reads there exactly like one that exited — which is how a *running*
    conversation came to be offered for resume. `held_ids` is the CLI's own
    answer and catches what the registry cannot see, including a session whose
    `agent_runs` row says the run exited while its process outlived the reap.
    None from it means the CLI could not be asked, never that nothing is
    running, so it must not subtract from what the registry established.

    It is a callable, and it is asked LAST, because it is the only check here
    that costs a process spawn: every candidate the free checks already
    settled — no transcript, a pane of its own, a run this process holds —
    must not pay for it.
    """
    return not _refusal(session_id, run_trace, provider, held_ids)


def is_resumable(session_id: str) -> bool:
    """Whether ONE session could be continued right now.

    `_offerable`'s rule asked about a single id, so the session page and the
    launch sheet cannot disagree about what can be continued — a page that
    showed Resume where the sheet hides it would send the operator at a launch
    the route then refuses.

    It adds the tier check the picker does not need: the sheet reads `enabled`
    off launch-options beside the list, while this answers a lone button that
    would otherwise offer a launch the route can only refuse.

    `sdk-…` ids answer False: the CLI has no session under regin's own name for
    a run, and the child id is the one `--resume` understands.
    """
    return not resume_refusal(session_id)


def resume_refusal(session_id: str) -> str:
    """`is_resumable`'s answer with its reason — '' where it says yes.

    The trace page asks for the reason, not just the verdict: a session that is
    neither steerable nor resumable renders no composer and no button, and
    which of those it is decides whether that emptiness is a dead conversation
    or one the CLI is still holding.
    """
    from lib.providers import get_active_provider
    from lib.settings import settings

    if not session_id or session_id.startswith(_SYNTHETIC_PREFIX):
        return 'synthetic'
    if not settings.agent_sdk.enabled:
        return 'tier_off'
    from lib.agent_sdk import store

    provider = get_active_provider()
    run = store.find_run(session_id)
    return _refusal(session_id, run["trace_id"] if run else None,
                    provider, provider.live_session_ids)


def _entry(row, run: tuple[str, str] | None) -> dict:
    """One picker row. `title`/`cwd` are normalised to strings so the client
    renders a missing one as blank rather than the word "None".

    `run_trace_id` is the run's `sdk-…` half, carried because it is the id the
    launch route returns for a continued run: without it a client cannot tell
    "the session I am already looking at" from "a different one" and would
    navigate away from a card that was already showing the right session.

    `model` is the one the run was held on, so a continuation can stay on it
    instead of silently dropping to the install default — a change of model
    mid-conversation is not something the operator asked for or would see.
    Blank for a terminal session, which regin recorded no model for.
    """
    run_trace, model = run or ('', '')
    return {
        'session_id': row.trace_id,
        'title': row.title or '',
        'cwd': row.cwd or '',
        'last_seen': row.last_seen,
        'prompts': row.prompts,
        'kind': 'run' if run_trace else 'session',
        'run_trace_id': run_trace,
        'model': model,
    }


def list_resumable(query: str = "", limit: int = 30) -> list[dict]:
    """Sessions the launch sheet may offer, newest first.

    Each entry carries the `cwd` it ran in, which the client needs as much as
    the id: `claude --resume <id>` resolves the session relative to the process
    working directory, so continuing one from the wrong cwd fails to find it.

    `kind` is served derived, for the same reason `resumable` is on the single-
    run route — whether a pick continues the trace or opens a new one is the
    launch route's rule, and a client re-deriving it would drift.
    """
    from lib.providers import get_active_provider

    rows = _candidate_rows(query.strip(), max(1, limit))
    runs = _runs_by_child([r.trace_id for r in rows])
    provider = get_active_provider()
    # Memoized for the page, not asked once per candidate: the probe shells out
    # to the CLI, and this loop runs over the whole overfetched window.
    held = functools.lru_cache(maxsize=1)(provider.live_session_ids)

    out: list[dict] = []
    for row in rows:
        if len(out) >= limit:
            break
        run = runs.get(row.trace_id)
        if _offerable(row.trace_id, run[0] if run else None, provider, held):
            out.append(_entry(row, run))
    return out
