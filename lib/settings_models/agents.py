"""Agent messaging, bridge, SDK and task blocks of `Settings`."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

_SEVERITY = Literal[
    "progress", "note", "lesson", "result", "summary", "warning", "blocker"
]


class AgentMessagesConfig(BaseModel):
    """The `send_to_user` agent → human channel (inbox + push channels).

    Persistence and the in-app inbox are always on; outbound **push
    channels** are opt-in. Each channel is gated by its own
    `*_min_severity`: only messages at or above that severity in
    `lib.orm.models.agent_messages.MESSAGE_TYPES` are delivered, so a
    background run can reach you on a `blocker` without spamming you on
    every `progress` line. A message fans out to *every* configured
    channel whose gate it clears.

    Channels live in `lib/agent_messages/push/`; each reads its own flat
    fields below. Adding one is a new `PushChannel` subclass + a registry
    entry + its config fields here — no change to the dispatch path.

    `base_url` is woven into each payload so the notification links
    straight back to the originating session in the regin UI.
    """

    base_url: str = "http://127.0.0.1:8321"

    # Each channel also carries a `{channel}_enabled` switch so a fully
    # configured channel can be muted without deleting its URL/token.
    # Default True: a configured channel delivers unless explicitly muted.

    # ── Generic webhook channel (ntfy / Slack incoming hook / phone) ──
    webhook_enabled: bool = True
    webhook_url: str | None = None
    webhook_min_severity: _SEVERITY = "warning"
    webhook_timeout_seconds: float = 5.0

    # ── Telegram channel (Bot API sendMessage) ──
    # Create a bot via @BotFather for the token; `chat_id` is your user or
    # group id (talk to the bot once, then read it from getUpdates).
    telegram_enabled: bool = True
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_min_severity: _SEVERITY = "warning"
    telegram_timeout_seconds: float = 5.0

    # ── Lark / Feishu channel (custom-bot incoming webhook) ──
    # In a Lark group: Settings → Bots → Add Bot → Custom Bot → copy the
    # webhook URL into `lark_webhook_url`. If you enable the bot's
    # "signature verification", put that secret in `lark_secret` (the
    # channel then signs each request); leave it None otherwise.
    lark_enabled: bool = True
    lark_webhook_url: str | None = None
    lark_secret: str | None = None
    lark_min_severity: _SEVERITY = "warning"
    lark_timeout_seconds: float = 5.0

    # ── Interaction-event pushes (opt-in) ──
    # Beyond agent-authored `send_to_user`, surface the moments where the
    # agent is *blocked waiting on you* as inbox cards that also fan out to
    # the channels above. Off by default — permission prompts can be frequent.
    # `push_permission_events`: a pending permission prompt / AskUserQuestion
    #   (recorded as a `blocker`). `push_plan_events`: a plan ready for review
    #   on ExitPlanMode (recorded as a `warning`). Each still rides the
    #   per-channel severity gate, so a channel set to `blocker` gets prompts
    #   but not plans.
    push_permission_events: bool = False
    push_plan_events: bool = False

    # ── Rate-limit alerts ──
    # `regin usage measure` (the regin-meter mod) raises one `warning` when a
    # rate-limit window's percent used reaches one of these thresholds —
    # once per (window kind, reset time, threshold) across every session.
    rate_limit_alert_at: list[int] = Field(default_factory=lambda: [80, 95])

    # ── Event bus overrides ──
    # Per-kind enable/disable for the declared notifiable events in
    # `lib.agent_messages.events.REGISTRY` (proposal.ready, content.drift,
    # grade.finished, …). A `{kind: bool}` here overrides that kind's
    # registry default; kinds absent from the map keep their default (the two
    # interaction kinds also honor the `push_*_events` booleans above for
    # back-compat). Enumerate the full catalog with `regin events list`.
    events: dict[str, bool] = Field(default_factory=dict)

    # ── Retention (opt-in) ──
    # The inbox is otherwise grow-forever. When `retention_days` is set,
    # messages older than that are hard-deleted automatically after each
    # write (see `store._enforce_retention`). None = keep forever (default,
    # original behavior). `retention_keep_pinned` shields pinned cards from
    # the auto-prune. Manual `regin messages prune` is always available.
    retention_days: int | None = None
    retention_keep_pinned: bool = True


class AgentBridgeConfig(BaseModel):
    """The agent bridge: HTTP `POST` → guarded tmux keystroke injection into
    a live claude session's pane (`lib/agent_bridge`, `docs/agent-bridge-design.md`).

    Off by default. This endpoint is remote keystroke injection into a
    terminal — the `token` is equivalent to SSH access to the machine, so it
    lives only in the gitignored `config/settings.local.json`, never in the
    tracked `config/settings.json`. Delivery is bounded per-session by
    `rate_limit_per_minute`; each message is sanitized and capped at
    `max_text_len` before it is typed literally into the pane. Only panes
    whose foreground process is in `allowed_pane_commands`, or in a provider's
    declared `pane_commands`, accept delivery — the guard that stops a message
    typed at a fallen-back shell from executing as a shell command (native
    builds report `claude.exe`; NVM installs report `claude` or `node`; kimi
    reports `node`). The list adds to the providers' declarations; it cannot
    remove one.
    """

    enabled: bool = False
    # Full, unfiltered shell access as the user running `regin serve`, over a
    # WebSocket-attached PTY (`lib/agent_bridge/terminal.py`). This is not the
    # guarded, allowlisted steering the rest of this config describes: a real
    # terminal has no key allowlist, no rate budget and no sanitizer, because
    # any of those would only be false comfort against `tmux send-keys`, which
    # can run anything. The two controls are therefore binary and deliberate —
    # this flag, off by default, and the admin role the route demands. Its own
    # switch rather than a mode of `enabled`, so mobile steering can stay on
    # while raw terminal access stays off.
    #
    # Visible side effect worth knowing before you turn it on: while a browser
    # terminal is open, the pane's window follows ITS size, not the operator's.
    # tmux gives a window one size and resolves it to the latest client
    # (`window-size`), and the grouped session the browser attaches to shares
    # that window — measured on tmux 3.6, 80x24 became 200x49 for as long as a
    # 200x50 browser client was attached, and reverted the moment it closed.
    terminal_enabled: bool = False
    token: str = ""
    rate_limit_per_minute: int = 30
    max_text_len: int = 4000
    allowed_pane_commands: list[str] = Field(
        default_factory=lambda: ["claude", "claude.exe", "node"])
    # tmux socket for panes regin creates itself, to take over a conversation
    # the CLI holds off-pane (`lib/agent_bridge/attach.py`). Its own server, not
    # the operator's: it must work when they are not running tmux at all, it
    # must not appear in their window list, and their `kill-server` must not
    # take regin's panes with it. Every read/write already threads a socket
    # (`bridge_panes.tmux_socket`), so a second one costs nothing.
    attach_socket: str = "regin-bridge"


class AgentSdkConfig(BaseModel):
    """Sessions regin *launches* through the Claude Agent SDK (`lib/agent_sdk`).

    Off by default. This is a strictly larger capability than the rest of
    regin: everything else observes a session the user drives, whereas this
    starts a `claude` process and holds its input stream. The payoff is a
    typed control channel — an `AskUserQuestion` is answered by returning
    `updated_input` from the SDK's permission callback, instead of the tmux
    tier's keystroke walk through the question widget.

    `cli_path` is empty by default, meaning "resolve `claude` off PATH". Leave
    it that way unless the user's CLI is somewhere unusual: the SDK ships its
    own `claude` inside its platform package and will silently prefer that
    one, so regin would trace a build the user never installed.

    `max_concurrent_runs` bounds live runners — each is a real child process,
    and an unbounded launcher is a fork bomb with extra steps.

    `idle_timeout_sec` is 0 — wait forever — because only interactive runs
    ever wait between turns (`one_shot` runs close their queue at launch), and
    an interactive run belongs to the operator until they stop it. A quiet
    stream is also not proof of a quiet session: background subagents and
    long shell tasks emit no frames while they work, so any idle timer here
    can kill their child mid-run (it did — the frame guard in
    `runner._await_prompt` never saw them). Set it only for a run nobody is
    attending, accepting that in-flight background work dies with it; the
    reclaim path for an abandoned session is the operator's Stop in `/live`.

    `stop_grace_sec` bounds how long a stop waits on an interrupted turn to end
    itself before abandoning it. The SDK ends a response stream on the CLI's
    result message and otherwise iterates indefinitely, so without a timer a
    wedged turn would make `stop` a promise nothing keeps.

    `teardown_settle_sec` is the lull a *graceful* teardown reads as "the child
    has stopped talking". Two top-level turns can be owed results at once — the
    operator's and the one a finished background task woke the agent for — and
    no field on a `ResultMessage` says which turn it ends, so the first result
    to arrive ends the pump's wait whichever turn it really belonged to.
    Disconnecting on it cuts off an answer that is still streaming; for a
    `one_shot` run that is a silent false success.

    `max_buffer_mb` bounds a single NDJSON message read off the child's stdout.
    The SDK's own default is 1 MiB, which a real session exceeds routinely — one
    Bash heredoc or file result over the line kills the *reader*, and the reader
    is the session's only stream, so the run ends `failed` and the operator has
    to resume by hand. 0 defers to the SDK's default.

    A lull alone is a weak signal — measured against a live child, 15% of gaps
    between frames exceeded a quarter-second and the gap from a task settling
    to the wake turn's first frame was 6.4s. So a lull only ends the wait once
    no delegated task is still in flight; while one is, the wait runs to the
    `stop_grace_sec` deadline instead, because the agent has work it can still
    be woken to speak about. An operator's Stop never waits: they asked for the
    session to end. 0 disables the wait, restoring the immediate disconnect.

    `gate_plan` and `gated_tools` extend the typed channel from answering
    questions to *deciding* — an `ExitPlanMode` held for approval, and named
    tools held for an allow/deny from `/live` (`lib/agent_sdk/policy.py`). Both
    default to nothing gated, so an existing install keeps parking only
    `AskUserQuestion`. `gated_tools = ["*"]` gates every call, which is the
    only way to cover tools regin cannot enumerate (MCP tools arrive with
    server-specific names).

    **Gating is a veto the CLI offers, not a hook regin controls.** The
    callback is consulted only for calls the CLI would otherwise prompt about,
    so an allowlisted pattern or a sandbox-approved read-only Bash never
    reaches the policy — measured: three `Bash echo` calls invoked it zero
    times, three `Write` calls three times. A shadowing `permission_mode`
    (`acceptEdits`, `bypassPermissions`, `dontAsk`) bypasses it; `shadowed_gating()`
    reports that combination, because a security control that silently does
    nothing is worse than one that refuses.

    **A park has no clock.** A held question waits for the human, however
    long that takes: expiring one declines a call the operator never saw and
    reports it to the model as a refusal, so the agent goes on to narrate a
    decision nobody made. The cost is real and accepted — an unattended park
    holds its worker and a `max_concurrent_runs` slot until it is answered or
    the run is stopped from `/live`.
    """

    enabled: bool = False
    cli_path: str = ""
    model: str = ""
    # Empty defers to the CLI's own default, exactly as `model` does. Set only
    # to pin a reasoning-effort floor for every run this install launches; the
    # launch sheet's per-run pick overrides it.
    effort: str = ""
    max_concurrent_runs: int = 4
    permission_mode: str = "default"
    idle_timeout_sec: int = 0
    stop_grace_sec: int = 10
    # ge=0: a negative ceiling makes the transport's guard raise on the first
    # frame, which is the very failure this field exists to prevent.
    max_buffer_mb: int = Field(32, ge=0)
    teardown_settle_sec: float = 0.25
    gate_plan: bool = False
    gated_tools: list[str] = Field(default_factory=list)

    def shadowed_gating(self, mode: str = "") -> str:
        """The reason gating is inert, or "" when it can take effect.

        `bypassPermissions` at least makes the SDK warn; `acceptEdits` is
        silent, so an operator can configure `gated_tools` and get a UI that
        promises approval over a runtime that never asks. `dontAsk` is inert
        the other way round — "deny if not pre-approved" means the call is
        refused instead of parked, so the operator is never asked either.

        `mode` is a per-run override (`RunOptions.permission_mode`), which wins
        over this config — so a report keyed only on the configured mode would
        miss exactly the case an operator chose at launch time.
        """
        if not (self.gate_plan or self.gated_tools):
            return ""
        effective = mode or self.permission_mode
        if effective in ("acceptEdits", "bypassPermissions"):
            return (f"permission_mode={effective!r} skips the "
                    "permission callback, so nothing is gated")
        if effective == "dontAsk":
            return ("permission_mode='dontAsk' denies anything not "
                    "pre-approved instead of asking, so nothing is gated")
        return ""


class TasksConfig(BaseModel):
    """Task capture: getting an idea into regin from anywhere (`lib/tasks`).

    Off by default. `capture_enabled` arms `POST /api/tasks/capture`, the one
    route authenticated by an API key rather than the web-UI JWT — while it is
    off the route 404s no matter what key is presented, mirroring the bridge.

    A capture key is a deliberately small credential: a captured task always
    lands with `status='idea'` and can carry no execution parameter, so the
    worst a leaked one can do is add backlog rows. `capture_rate_limit_per_minute`
    bounds even that, per key.
    """

    capture_enabled: bool = False
    capture_rate_limit_per_minute: int = 30


class TaskIntegrationsConfig(BaseModel):
    """Third-party issue trackers a task can link to (`lib/tasks/integrations`).

    Every tracker is off until it holds a credential, and an unconfigured one
    claims no reference — so the attach surface can only ever resolve a paste
    to a platform the operator actually wired up.

    Each platform reads its own flat `{platform}_*` fields here, the way
    `AgentMessagesConfig` holds one block per push channel. Adding the second
    tracker is a new `IssueTrackerProvider` subclass, a registry entry, and its
    fields below — the dispatch path does not change.

    The block is written to **local** settings: `linear_api_key` is a secret
    that must not land in a git-tracked settings.json.
    """

    linear_enabled: bool = False
    linear_api_key: str | None = None
    linear_timeout_seconds: float = 10.0
