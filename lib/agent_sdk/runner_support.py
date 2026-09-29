"""Value types and pure helpers the session runner is built from: the turn
record, the background-task ledger, API-retry state, queued prompts and the
query payload.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from lib.activity_log import get_activity_logger
from lib.agent_events import PermissionRequested
from lib.agent_events.ask import ask_questions
from . import policy


log = get_activity_logger("agent_sdk")


# Pushed into the prompt queue to end the pump. A prompt is always a non-empty
# string, so None can never collide with real input.
_CLOSE = None


class RunnerBusy(RuntimeError):
    """`max_concurrent_runs` reached."""


@dataclass
class _Turn:
    """One turn in flight, and the identity every row it produces carries.

    `context` lives here rather than on the session because the last API call
    the previous turn made is not this turn's context size, and a usage row is
    stamped with the turn it belongs to.

    `done` is the pump's future. Only a prompted turn has one: it is the only
    turn anybody is waiting on.
    """

    index: int
    done: asyncio.Future | None = None
    context: int | None = None
    # A placeholder holding the floor for an abandoned turn's late result —
    # see `_leave_a_sink`. Not work anyone is waiting on, so it must not read
    # as the agent working.
    sink: bool = False
    # Monotonic, because this is only ever read as a duration: a wall clock
    # stepped mid-turn would render an elapsed time that ran backwards.
    started: float = field(default_factory=time.monotonic)
    # tool_use_id → name for the calls this turn has open. Per-turn, because
    # the session-wide `_tool_names` ledger is emptied only by a result: a
    # call abandoned mid-flight — interrupted, or denied — stays in it for the
    # life of the session, and every later turn would report it as running.
    tools: dict = field(default_factory=dict)


# `claude_agent_sdk._internal.query.DEFERRING_TASK_TYPES`. Copied rather than
# imported: it is private to the SDK, and this module must stay importable
# where the SDK is not installed.
_TRACKED_TASK_TYPES = frozenset({"local_agent", "local_workflow"})


# `claude_agent_sdk.types.TERMINAL_TASK_STATUSES` — every status meaning the
# task has finished. A killed task is finished too: leaving it tracked would
# pin the teardown gate open for the rest of the session.
_TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "stopped",
                                     "killed"})


class _TaskLedger:
    """Which delegated background tasks are still running.

    A delegated agent task's completion wakes the parent for a follow-up turn
    that ends in its own top-level result; the SDK's own stdin close depends on
    that extra result existing (`query.wait_for_result_and_end_input`). Which
    turn any given result ends is not this ledger's business — nothing on a
    `ResultMessage` says, and the runner keeps one turn open at a time so it
    never has to guess. What is read from here is whether the agent still has
    work that can speak, which is what the teardown gate needs.

    Only delegated agent work counts. A background *shell* —
    `Bash(run_in_background=True)`, `tail -f` — travels the same frames but may
    never reach a terminal status, so tracking one would pin that gate open for
    the life of the session. Terminal state arrives as a `task_notification`
    *or* a `task_updated` patch and not reliably both, so either can retire a
    task — on its status, never on which frame carried it, since `task_updated`
    also reports a task merely starting or pausing.
    """

    def __init__(self) -> None:
        self._tracked: set[str] = set()

    def observe(self, message) -> None:
        """Track one lifecycle frame."""
        task_id = getattr(message, 'task_id', '') or ''
        subtype = getattr(message, 'subtype', '') or ''
        if not task_id:
            return
        if subtype == 'task_started':
            self._track(task_id, message)
        elif subtype in ('task_notification', 'task_updated'):
            self._settle(task_id, message)

    def _track(self, task_id: str, message) -> None:
        data = getattr(message, 'data', None) or {}
        if data.get('task_type') in _TRACKED_TASK_TYPES:
            self._tracked.add(task_id)

    def _settle(self, task_id: str, message) -> None:
        """Retire a task that has reached a terminal status, and only then.

        `task_updated` reports every state change, not just the last one, so
        the status decides this and not the frame it arrived on — clearing a
        `running` patch would send the teardown gate back to trusting a lull
        while the agent still has work that can speak. A task never tracked —
        a background shell — is not retired by reaching one either, since it
        was never held against the gate to begin with.
        """
        if self._status(message) not in _TERMINAL_TASK_STATUSES:
            return
        self._tracked.discard(task_id)

    @staticmethod
    def _status(message) -> str:
        """A task's status, from whichever frame carried it."""
        patch = getattr(message, 'patch', None)
        if isinstance(patch, dict) and patch.get('status'):
            return str(patch['status'])
        return str(getattr(message, 'status', '') or '')

    @property
    def in_flight(self) -> bool:
        """Whether delegated work the agent could still speak about is running.

        A task that has not reported terminal yet is one the agent may still be
        woken by, and the gap between that wake and its first frame is measured
        in seconds — far longer than any lull a teardown should read as "the
        child has finished talking".
        """
        return bool(self._tracked)

    def forget(self) -> None:
        self._tracked.clear()


@dataclass(frozen=True)
class RetryState:
    """One `api_retry` frame: the CLI could not reach the API and will try again.

    Deliberately shaped as the retry fields a terminal session's status-line
    banner already fills (`agent_bridge.screen_state`), because the two tiers
    describe the same incident and the card renders whichever it is handed. A
    field the frame did not carry stays None rather than defaulted, so "the CLI
    said nothing" is distinguishable from a zero it did say.
    """

    label: str
    wait: str
    attempt: int | None
    max_retries: int | None
    no_response: bool


def _retry_wait(ms) -> str:
    """`retry_delay_ms` as the duration a status-line banner would print.

    Matched to `screen_state`'s vocabulary (`8s`, `1m 20s`) rather than
    invented, since both tiers feed `retry_in` and the card must not read
    differently depending on which one is driving.
    """
    secs = max(0, round(int(ms or 0) / 1000))
    if secs < 60:
        return f"{secs}s"
    minutes, rest = divmod(secs, 60)
    return f"{minutes}m" if not rest else f"{minutes}m {rest}s"


def _retry_state(data: dict) -> RetryState:
    """One `system`/`api_retry` frame as the state the card renders.

    `no_response` is the branch that matters here: the request got no answer at
    all rather than an error the server sent back, which is what a degraded
    network produces — and its wait lives under `no_response.retry_wait_ms`,
    not in the top-level delay, which that branch leaves at the base backoff.
    """
    no_response = data.get("no_response")
    quiet = isinstance(no_response, dict)
    wait_ms = (no_response.get("retry_wait_ms") if quiet
               else None) or data.get("retry_delay_ms")
    attempt = data.get("attempt")
    max_retries = data.get("max_retries")
    status = data.get("error_status")
    error = str(data.get("error") or "").strip()
    return RetryState(
        # The CLI's own words where it gave any; its banner uses the bare
        # status code when the body carried no message, so the fallback keeps
        # a bare frame from rendering a label-less banner.
        label=error or (f"API error {status}" if status else "API error"),
        wait=_retry_wait(wait_ms),
        attempt=int(attempt) if isinstance(attempt, int) else None,
        max_retries=int(max_retries) if isinstance(max_retries, int) else None,
        no_response=quiet,
    )


def _live_verb(turn_open: bool, delegated: bool) -> str | None:
    if turn_open:
        return 'Working'
    return 'Waiting on subagent' if delegated else None


def _retry_fields(retry: "RetryState | None") -> dict:
    """A retry as the `ScreenState` fields a terminal banner would fill.

    Shared shape rather than a second code path, so the card's retry strip
    renders a run regin launched and a pane it merely watches identically.
    `retry_final` is never set: it means "the screen named no attempt number",
    and an `api_retry` frame always names one.
    """
    return {
        'retry_label': retry.label if retry else None,
        'retry_in': retry.wait if retry else None,
        'retry_attempt': retry.attempt if retry else None,
        'retry_max': retry.max_retries if retry else None,
    }


# Frames that prove the API answered. A `UserMessage` does not: a tool result
# is produced locally and travels the stream whether or not the network is up,
# so clearing on one would report a healthy session between the failed request
# and the retry that has not been attempted yet.
_API_ALIVE_FRAMES = frozenset({"AssistantMessage", "StreamEvent",
                               "ResultMessage"})


class _RetryLatch:
    """Whether the CLI is currently between a failed API call and its retry.

    Event-driven on both edges, which is what makes this honest where a
    silence timer is not: the CLI announces the failure (`system/api_retry`)
    and announces the recovery by simply speaking again. A stalled turn is
    otherwise indistinguishable from a long tool call — both emit nothing —
    and that ambiguity is why `turn_in_flight` alone reports `working` through
    an outage.

    Only the LATEST frame is held. The CLI re-emits `api_retry` per attempt
    with a rising `attempt`, so the newest one is the whole story and a history
    of superseded attempts would only have to be trimmed.
    """

    def __init__(self) -> None:
        self._state: RetryState | None = None

    def observe(self, message) -> None:
        if getattr(message, "subtype", "") == "api_retry":
            self._state = _retry_state(getattr(message, "data", None) or {})
            return
        if type(message).__name__ in _API_ALIVE_FRAMES:
            self._state = None

    def forget(self) -> None:
        self._state = None

    @property
    def state(self) -> RetryState | None:
        return self._state


def _enrich_ask(span: dict | None, event) -> None:
    """Carry the question structure onto an ask's span.

    Without it the `/live` sheet has no options to render and falls back to
    read-only, so the tier's own questions would be unanswerable.
    """
    if not span or getattr(event, 'tool_name', '') != 'AskUserQuestion':
        return
    questions = ask_questions(getattr(event, 'tool_input', None) or {})
    if questions:
        span['attributes']['questions'] = questions


def _enrich_permission(span: dict | None, event) -> None:
    """Carry what is being asked onto a parked call's span.

    A decision card that can only name the tool asks an operator to approve
    `tool.Bash` with no command in sight, which is not a decision at all.
    """
    if not span or not isinstance(event, PermissionRequested):
        return
    if event.kind == 'question':
        return
    span['attributes'].update(
        policy.request_attrs(event.tool_name, event.tool_input, event.kind))


# `eq=False` so identity is identity: two queued "continue"s are two distinct
# entries, and field equality would let a list removal take the wrong one.
@dataclass(eq=False)
class _Queued:
    """One prompt waiting behind the running turn.

    A record rather than the bare string it used to be, because `/live` needs
    to name *one* entry to edit or drop and two identical prompts are two
    entries — position is not identity across the poll that renders them and
    the request that acts on one.

    Leaving `_pending` is the cutoff for both mutations, and the pump takes a
    record off it under the same lock the mutations hold — so an edit either
    lands before the CLI is given the text or finds nothing to edit, with no
    third outcome where it rewrites a prompt already sent.

    Cancelling is a flag rather than a removal because the record is by then
    inside an `asyncio.Queue`, which cannot be reached into; the pump skips it
    on the way past instead.
    """

    id: str
    text: str
    cancelled: bool = False
    # The `bridge_messages` row the composer recorded for this prompt, so a
    # prompt abandoned after `submit_prompt` already acked "prompt queued" can
    # correct that row instead of leaving it reading delivered forever.
    row_id: int | None = None
    # Set by `_report_drop` so an abandonment is reported once even when two
    # paths reach the same record (a stop cancels it, then the pump skips it).
    dropped: bool = False
    # Images the composer staged for this prompt, as the CLI's stream-json
    # input wants them: `{media_type, data_b64}` per entry. They ride the
    # record rather than the text because the prompt SPAN must stay the plain
    # sentence the operator typed — the bytes belong to the turn, not to the
    # trace's copy of what was said.
    images: list = field(default_factory=list)


def _query_payload(text: str, images: list):
    """What to hand `ClaudeSDKClient.query` for one prompt.

    A prompt with no images stays the bare string it has always been — the SDK
    wraps that itself, and routing it through the block form instead would
    change the shape of every existing turn to carry an image feature nobody
    asked for.

    With images it becomes a one-message async iterable, because that is the
    only form `query` writes through verbatim: the dict lands on the CLI's
    stream-json stdin exactly as built, so the content blocks reach the model
    as a real image rather than as a path it would have to go and read. The
    text block goes last — the image is the thing being asked about, and a
    question reads better after its subject.
    """
    if not images:
        return text
    content = [{"type": "image",
                "source": {"type": "base64",
                           "media_type": img["media_type"],
                           "data": img["data_b64"]}}
               for img in images]
    content.append({"type": "text", "text": text})

    async def _once():
        yield {"type": "user",
               "message": {"role": "user", "content": content},
               "parent_tool_use_id": None}

    return _once()
