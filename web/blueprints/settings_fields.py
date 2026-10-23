"""Field registries for the nested settings blocks the settings API round-trips."""

from __future__ import annotations


# Nested pydantic settings blocks (agent_memory, agent_messages) can't ride
# the flat SETTINGS_SCHEMA mechanism — that writes flat keys pydantic won't
# read back. Each is round-tripped whole (like rule_trigger_thresholds): the
# field registries below drive a generic GET/PUT per block, and the UI renders
# one card per `group`. Field `type`: bool | int | float | string | choice
# (needs `options`); `min`/`max`/`step` apply to numbers.
_AGENT_MEMORY_FIELDS: list[dict] = [
    # ── General ──
    {"key": "enabled", "group": "General", "type": "bool",
     "label": "Memory engine enabled",
     "description": "Master switch for the cross-session memory engine."},
    {"key": "auto_inject", "group": "General", "type": "bool",
     "label": "Auto-inject recalled experience",
     "description": "Inject matching past experience into each prompt as a "
                    "<recalled_experience> block."},
    {"key": "dense_enabled", "group": "General", "type": "bool",
     "label": "Dense recall on long-lived surfaces",
     "description": "Use embedding + cross-encoder recall on the MCP/web/CLI "
                    "surfaces (degrades to FTS when the models are missing)."},
    {"key": "scope_policy", "group": "General", "type": "choice",
     "options": ["global", "per-repo", "per-repo-tagged"],
     "label": "Scope policy",
     "description": "How captures are scoped and recall narrowed. "
                    "per-repo-tagged: stamp the repo on writes, recall globally."},
    # ── Distillation ──
    {"key": "distill_min_importance", "group": "Distillation", "type": "float",
     "min": 0, "max": 1, "step": 0.05, "label": "Drop below importance",
     "description": "Distilled drafts the LLM self-scores below this are "
                    "dropped, never stored. Higher = more selective."},
    {"key": "auto_approve_importance", "group": "Distillation", "type": "float",
     "min": 0, "max": 1, "step": 0.05, "label": "Auto-approve at/above importance",
     "description": "Drafts scored at/above this skip the human review queue "
                    "and become active. The gray band stays 'proposed'."},
    # ── Recall ──
    {"key": "recall_min_score", "group": "Recall", "type": "float",
     "min": 0, "max": 1, "step": 0.05, "label": "Min rerank score",
     "description": "Minimum cross-encoder confidence for a reranked hit to "
                    "surface. FTS/RRF results are rank-gated by top-k instead."},
    {"key": "recall_quality_weighting", "group": "Recall", "type": "bool",
     "label": "Quality-weight recall ranking",
     "description": "Re-rank recalls by importance · veracity · deliberate-recall "
                    "count · recency, not lexical/dense match alone."},
    {"key": "recall_mode", "group": "Recall", "type": "choice",
     "options": ["inline", "subagent"],
     "label": "Deliberate recall mode",
     "description": "inline: the main agent infers intent and calls recall "
                    "directly. subagent: it dispatches a memory-research "
                    "subagent that sifts candidates in isolated context and "
                    "returns a digest — keeps the search out of main context. "
                    "Orthogonal to the always-on auto-inject hook."},
    {"key": "recall_recency_half_life_days", "group": "Recall", "type": "float",
     "min": 0, "step": 1, "label": "Recency half-life (days)",
     "description": "How fast a memory's recency weight decays in recall "
                    "ranking; a deliberate recall resets its clock. 0 = off."},
    # ── Auto-inject ──
    {"key": "inject_top_k", "group": "Auto-inject", "type": "int", "min": 1, "step": 1,
     "label": "Max memories per prompt",
     "description": "How many recalled memories the auto-inject block carries."},
    {"key": "inject_max_chars", "group": "Auto-inject", "type": "int",
     "min": 0, "step": 100, "label": "Inject block budget (chars)",
     "description": "Character budget for the rendered <recalled_experience> block."},
    {"key": "inject_min_overlap", "group": "Auto-inject", "type": "int",
     "min": 0, "step": 1, "label": "Min token overlap to inject",
     "description": "A memory must share at least this many distinct content "
                    "tokens with the prompt to auto-inject (BM25 always ranks "
                    "something). 0 disables the gate."},
    {"key": "inject_dedup_session", "group": "Auto-inject", "type": "bool",
     "label": "Dedup within a session",
     "description": "Skip re-injecting a memory already injected earlier this "
                    "session; a re-match instead reinforces it once."},
    {"key": "inject_dense_via_server", "group": "Auto-inject", "type": "bool",
     "label": "Dense inject via warm server",
     "description": "Borrow the warm `regin serve` for dense + rerank recall at "
                    "inject time (loopback POST), falling back to in-process FTS."},
    {"key": "inject_server_url", "group": "Auto-inject", "type": "string",
     "label": "Warm server URL",
     "description": "Base URL of the serve process the inject hook borrows."},
    {"key": "inject_server_timeout_seconds", "group": "Auto-inject", "type": "float",
     "min": 0, "step": 0.1, "label": "Inject recall timeout (s)",
     "description": "How long the inject hook waits on the warm server before "
                    "falling back to FTS."},
    {"key": "trace_recall", "group": "Auto-inject", "type": "bool",
     "label": "Trace injections",
     "description": "Record each injection as a memory.recall span on the "
                    "session trace for per-prompt auditability."},
    {"key": "inject_skip_commands", "group": "Auto-inject", "type": "list",
     "label": "Skip auto-inject on these commands",
     "description": "Slash commands whose own machinery already pulls recall, so "
                    "the auto-inject block is redundant noise on them. Matched on "
                    "the command token only (first word), case-insensitively, "
                    "leading slash optional per entry — '/goal' never matches "
                    "'/goalpost'. Empty list = inject on every eligible prompt."},
    # ── Topic routing ──
    {"key": "topic_route_inject", "group": "Topic routing", "type": "bool",
     "label": "Inject routed topic context",
     "description": "Route each prompt through the authoritative topic graph and "
                    "prepend a pointer-only <topic_context> block (label + intent "
                    "+ ref paths) for the matched topic. The full wiki stays "
                    "opt-in via the /topic-router skill."},
    {"key": "topic_context_max_chars", "group": "Topic routing", "type": "int",
     "min": 0, "step": 100, "label": "Topic context budget (chars)",
     "description": "Character budget for the rendered <topic_context> block "
                    "(refs trimmed first, then the intent)."},
    {"key": "topic_route_querylog_min_queries", "group": "Topic routing",
     "type": "int", "min": 0, "step": 10,
     "label": "Query-log weighting min prompts",
     "description": "The keyword router down-weights words that saturate this "
                    "repo's own past prompts (on top of the always-on English-"
                    "frequency prior). It stays inert until the cached prompt "
                    "log reaches this many routed prompts, so a sparse log "
                    "can't distort routing. Very high = effectively disabled."},
    {"key": "topic_route_querylog_floor", "group": "Topic routing",
     "type": "float", "min": 0, "max": 1, "step": 0.05,
     "label": "Query-log weighting floor",
     "description": "Lower bound on the query-log down-weight multiplier. A "
                    "word in nearly every prompt shrinks to this factor but "
                    "never to zero, so a repo-ubiquitous term still counts a "
                    "little when it's the only hit."},
    # ── Consolidation ──
    {"key": "dream_enabled", "group": "Consolidation", "type": "bool",
     "label": "Dream (LLM consolidation)",
     "description": "reflect()'s single agentic stage: one call per run decides "
                    "every working row's fate, judges suspect pairs, and may "
                    "synthesize. Off (or no agent): surviving working rows are "
                    "blind-promoted and nothing else is judged."},
    {"key": "promote_allow_retire", "group": "Consolidation", "type": "bool",
     "label": "Let the dream retire rows",
     "description": "Honour the dream's drop/merge verdicts (reversible "
                    "supersede). Off: those verdicts degrade to 'hold' and the "
                    "model can only promote or keep a row working."},
    {"key": "forget_after_days", "group": "Consolidation", "type": "int",
     "min": 0, "step": 1, "label": "Forget never-recalled after (days)",
     "description": "reflect() retires episodic memories this old that were "
                    "never deliberately recalled (recall_count == 0). 0 disables."},
    {"key": "dedup_cosine_threshold", "group": "Consolidation", "type": "float",
     "min": 0, "max": 1, "step": 0.01, "label": "Dedup cosine threshold",
     "description": "reflect() merges two memories when their embedding cosine "
                    "similarity is at least this."},
    {"key": "dedup_text_threshold", "group": "Consolidation", "type": "float",
     "min": 0, "max": 1, "step": 0.01, "label": "Dedup text threshold",
     "description": "Fallback merge threshold on text similarity when no "
                    "embedder is available."},
    {"key": "contradiction_budget", "group": "Consolidation", "type": "int",
     "min": 0, "step": 1, "label": "Suspect pairs per dream",
     "description": "Cap on suspect episodic pairs (same scope, sharing a repo "
                    "file path) offered to one dream. Judged pairs are never "
                    "re-bought; 0 offers none without forgetting the ledger."},
    {"key": "decay_ignored_threshold", "group": "Consolidation", "type": "int",
     "min": 0, "step": 1, "label": "Decay after N ignored verdicts",
     "description": "reflect() decays an unproven episodic memory's importance "
                    "(−0.1/run, floored) once it draws this many feedback "
                    "'ignored' verdicts. Grade-time signal only. 0 disables."},
    {"key": "decay_injected_threshold", "group": "Consolidation", "type": "int",
     "min": 0, "step": 1, "label": "Decay after N un-reinforced injections",
     "description": "reflect() decays an unproven episodic memory once it was "
                    "auto-injected this many times with zero reinforcement "
                    "(always-on signal, no grade needed). 0 disables."},
]


_AGENT_MESSAGES_FIELDS: list[dict] = [
    {"key": "webhook_enabled", "group": "Webhook", "type": "bool",
     "label": "Enabled",
     "description": "Mute switch for this channel — turn it off to stop "
                    "deliveries without deleting the URL below."},
    {"key": "webhook_url", "group": "Webhook", "type": "string",
     "label": "Webhook URL",
     "description": "POST high-severity send_to_user messages here (ntfy / Slack "
                    "/ phone). Empty = webhook off. Stored machine-local."},
    {"key": "webhook_min_severity", "group": "Webhook", "type": "choice",
     "options": ["progress", "note", "lesson", "result", "summary",
                 "warning", "blocker"],
     "label": "Minimum severity",
     "description": "Only messages at or above this severity fire the webhook, "
                    "so a background run can page you on a blocker without spam."},
    {"key": "webhook_timeout_seconds", "group": "Webhook", "type": "float",
     "min": 0, "step": 0.5, "label": "Webhook timeout (s)",
     "description": "How long to wait on the webhook POST before giving up."},
    {"key": "telegram_enabled", "group": "Telegram", "type": "bool",
     "label": "Enabled",
     "description": "Mute switch for this channel — turn it off to stop "
                    "deliveries without deleting the token/chat id below."},
    {"key": "telegram_bot_token", "group": "Telegram", "type": "string",
     "label": "Bot token",
     "description": "Token from @BotFather. Empty = Telegram off. Stored "
                    "machine-local."},
    {"key": "telegram_chat_id", "group": "Telegram", "type": "string",
     "label": "Chat id",
     "description": "Target chat/user id (read it from the bot's getUpdates "
                    "after messaging it once). Required alongside the token."},
    {"key": "telegram_min_severity", "group": "Telegram", "type": "choice",
     "options": ["progress", "note", "lesson", "result", "summary",
                 "warning", "blocker"],
     "label": "Minimum severity",
     "description": "Only messages at or above this severity are sent to "
                    "Telegram."},
    {"key": "telegram_timeout_seconds", "group": "Telegram", "type": "float",
     "min": 0, "step": 0.5, "label": "Telegram timeout (s)",
     "description": "How long to wait on the Telegram API call before giving up."},
    {"key": "lark_enabled", "group": "Lark / Feishu", "type": "bool",
     "label": "Enabled",
     "description": "Mute switch for this channel — turn it off to stop "
                    "deliveries without deleting the webhook URL below."},
    {"key": "lark_webhook_url", "group": "Lark / Feishu", "type": "string",
     "label": "Custom-bot webhook URL",
     "description": "Incoming-webhook URL of a Lark group custom bot (group "
                    "Settings → Bots → Add Bot → Custom Bot). Empty = Lark off. "
                    "Stored machine-local."},
    {"key": "lark_secret", "group": "Lark / Feishu", "type": "string",
     "label": "Signing secret",
     "description": "Only if the bot has signature verification enabled — each "
                    "request is then signed. Leave empty otherwise."},
    {"key": "lark_min_severity", "group": "Lark / Feishu", "type": "choice",
     "options": ["progress", "note", "lesson", "result", "summary",
                 "warning", "blocker"],
     "label": "Minimum severity",
     "description": "Only messages at or above this severity are sent to Lark."},
    {"key": "lark_timeout_seconds", "group": "Lark / Feishu", "type": "float",
     "min": 0, "step": 0.5, "label": "Lark timeout (s)",
     "description": "How long to wait on the Lark webhook POST before giving up."},
    {"key": "push_permission_events", "group": "Interaction events", "type": "bool",
     "label": "Push permission prompts",
     "description": "Surface a pending permission prompt / AskUserQuestion as a "
                    "blocker inbox card that also fans out to the channels above."},
    {"key": "push_plan_events", "group": "Interaction events", "type": "bool",
     "label": "Push plan-ready",
     "description": "Surface a plan ready for review (ExitPlanMode) as a warning "
                    "inbox card that also fans out to the channels above."},
    {"key": "rate_limit_alert_at", "group": "Rate-limit alerts", "type": "list",
     "item": "int", "placeholder": "80", "add_label": "+ Add threshold",
     "label": "Alert at (% used)",
     "description": "A rate-limit window reaching one of these percentages "
                    "raises one warning (via the regin-meter mod), once per "
                    "window and threshold across all sessions."},
    {"key": "base_url", "group": "General", "type": "string",
     "label": "Base URL",
     "description": "Woven into every push payload so the notification links "
                    "back to the originating session in the regin UI."},
    {"key": "retention_days", "group": "Retention", "type": "int", "min": -1,
     "step": 1, "null_as": -1, "label": "Auto-prune after (days)",
     "description": "Hard-delete inbox messages older than this many days after "
                    "each send_to_user write, keeping the otherwise grow-forever "
                    "inbox bounded with no manual step. -1 = keep forever "
                    "(default). Manual `regin messages prune` is always available."},
    {"key": "retention_keep_pinned", "group": "Retention", "type": "bool",
     "label": "Keep pinned messages",
     "description": "Shield pinned inbox cards from the automatic age-based prune "
                    "above (they can still be removed manually)."},
]

_TOPIC_EVOLUTION_FIELDS: list[dict] = [
    # ── General ──
    {"key": "evolution_enabled", "group": "General", "type": "bool",
     "label": "Topic evolution enabled",
     "description": "Master switch for code-driven topic/memory co-evolution "
                    "(content-drift detection, refresh proposals, expiry). "
                    "Everything below is inert until this is on."},
    {"key": "mechanical_autoapply", "group": "General", "type": "bool",
     "label": "Auto-apply mechanical drift (rename-follow)",
     "description": "On commit, follow git renames into topic refs (written to "
                    "the local overlay, never the approved graph) and memory paths, and "
                    "cascade deleted refs onto linked memories."},
    {"key": "auto_spawn_agents", "group": "General", "type": "bool",
     "label": "Auto-spawn the drafting agent for refresh proposals",
     "description": "Hand content-drift refresh proposals to the external "
                    "drafting agent (needs a configured proposal agent). A real "
                    "cost — off by default even when evolution is on."},
    # ── Detection ──
    {"key": "content_drift_cosine", "group": "Detection", "type": "float",
     "min": 0, "max": 1, "step": 0.05, "label": "Content-drift cosine floor",
     "description": "When a changed ref file's embedding sits at/above this "
                    "cosine to its stored digest, the change is treated as "
                    "trivial and spared; below it, the topic is flagged drifted."},
    # ── Proposals ──
    {"key": "drift_proposal_batch_max", "group": "Proposals", "type": "int",
     "min": 0, "step": 1, "label": "Max proposals per evolve pass",
     "description": "Cap on refresh proposals emitted (and agents spawned) in "
                    "one evolve pass, so a large change can't flood the queue. "
                    "0 = unbounded."},
    {"key": "auto_proposal_expire_days", "group": "Proposals", "type": "int",
     "min": 0, "step": 1, "label": "Auto-expire unreviewed proposals (days)",
     "description": "Auto-generated proposals left unreviewed this many days are "
                    "retired (ignored) so the review queue can't rot. 0 = never."},
    {"key": "auto_review_notes", "group": "Proposals", "type": "bool",
     "label": "Auto-write LLM review notes on proposal runs",
     "description": "When a proposal run completes, an LLM reviewer attaches a "
                    "review note (regenerate/accept/dismiss recommendation) as a "
                    "feedback thread that carries into the next run. Needs a "
                    "configured proposal agent — a real cost, off by default even "
                    "when evolution is on."},
]


def _agent_sdk_fields() -> list[dict]:
    """Built rather than declared: `permission_mode`'s options are the CLI's own
    vocabulary, and restating them here would let the picker drift into offering
    a mode the launch surface rejects."""
    from lib.agent_sdk.client import EFFORT_LEVELS, PERMISSION_MODES
    return [
        # ── General ──
        {"key": "enabled", "group": "General", "type": "bool",
         "label": "SDK-launched sessions enabled",
         "description": "Master switch for sessions regin starts and owns "
                        "through the Claude Agent SDK. Strictly more capability "
                        "than the rest of regin: this spawns a claude process "
                        "and holds its input stream."},
        {"key": "cli_path", "group": "General", "type": "string",
         "label": "claude CLI path",
         "description": "Absolute path to the user's claude binary. Empty = "
                        "resolve off PATH. Leave empty unless the CLI is "
                        "somewhere unusual — the SDK otherwise prefers the "
                        "claude it ships, so traces would record a build that "
                        "was never installed."},
        {"key": "model", "group": "General", "type": "string",
         "label": "Model override",
         "description": "Model id for launched runs. Empty = whatever the CLI "
                        "would pick for itself."},
        {"key": "effort", "group": "General", "type": "choice",
         "options": ["", *EFFORT_LEVELS],
         "label": "Reasoning effort",
         "description": "Default reasoning effort for launched runs (a run may "
                        "override it from the /live launch sheet). Empty = "
                        "whatever the CLI would pick for itself."},
        {"key": "max_concurrent_runs", "group": "General", "type": "int",
         "min": 1, "step": 1, "label": "Max concurrent runs",
         "description": "Ceiling on live runners. Each is a real child "
                        "process, so an unbounded launcher is a fork bomb."},
        {"key": "permission_mode", "group": "General", "type": "choice",
         "options": list(PERMISSION_MODES),
         "label": "Permission mode",
         "description": "Default permission mode for launched runs (a run may "
                        "override it). acceptEdits, bypassPermissions and "
                        "dontAsk bypass the permission callback — with them, "
                        "nothing below is gated. plan runs no tools at all."},
        # ── Lifecycle ──
        {"key": "idle_timeout_sec", "group": "Lifecycle", "type": "int",
         "min": 0, "step": 60, "label": "Idle timeout (s)",
         "description": "How long a run with nothing to do keeps its child "
                        "process. A run stays open for follow-ups, so without "
                        "this an abandoned session holds a concurrency slot for "
                        "the life of the server. 0 waits forever."},
        {"key": "stop_grace_sec", "group": "Lifecycle", "type": "int",
         "min": 0, "step": 1, "label": "Stop grace (s)",
         "description": "How long a stop waits on an interrupted turn to end "
                        "itself before abandoning it, so a wedged turn can't "
                        "make stop a promise nothing keeps."},
        {"key": "teardown_settle_sec", "group": "Lifecycle", "type": "float",
         "min": 0, "step": 0.25, "label": "Teardown settle (s)",
         "description": "The lull a graceful teardown reads as 'the child has "
                        "stopped talking'. A background task finishing wakes "
                        "the agent for a turn of its own whose result is "
                        "indistinguishable from the prompt's, so without this "
                        "an answer still streaming is cut off. While a "
                        "delegated task is still running a lull proves "
                        "nothing, and the wait runs to the stop grace instead. "
                        "A Stop never waits. 0 disconnects immediately."},
        {"key": "max_buffer_mb", "group": "Lifecycle", "type": "int",
         "min": 0, "step": 1, "label": "Stream buffer (MiB)",
         "description": "Ceiling on one message read off the child's stdout. "
                        "The SDK's own default is 1 MiB, and a message over it "
                        "kills the reader — which is the session's only stream, "
                        "so the whole run ends failed and has to be resumed by "
                        "hand. 0 defers to the SDK default."},
        # ── Gating ──
        {"key": "gate_plan", "group": "Gating", "type": "bool",
         "label": "Hold ExitPlanMode for approval",
         "description": "Park a plan for an approve/reject decision from /live "
                        "instead of letting the run start building."},
        {"key": "gated_tools", "group": "Gating", "type": "list",
         "placeholder": "Bash", "add_label": "+ Add tool",
         "label": "Tools held for approval",
         "description": "Named tools parked for an allow/deny from /live, "
                        "matched exactly and case-sensitively ('bash' never "
                        "matches Bash). Empty = only AskUserQuestion parks. "
                        "'*' gates every call — the only way to cover tools "
                        "regin cannot enumerate, since MCP tools arrive with "
                        "server-specific names. Gating is a veto the CLI "
                        "offers, not a hook: a call the CLI would not have "
                        "prompted about (an allowlisted or sandboxed one) "
                        "never reaches it."},
    ]


def _agent_sdk_warnings(current) -> list[str]:
    reason = current.shadowed_gating()
    return [f"Gating is configured but inert: {reason}."] if reason else []


_AGENT_BRIDGE_FIELDS = [
    # `token` is deliberately absent: `_block_get` only returns declared
    # fields, so leaving it out is what keeps the bridge bearer token off the
    # browser — the invariant the design doc states — and `_block_put` merges
    # against the on-disk block, so omitting it here cannot drop it either.
    {"key": "enabled", "group": "Steering", "type": "bool",
     "label": "Message steering enabled",
     "description": "The guarded HTTP → tmux channel behind the /live "
                    "composer: sanitized message text and one allowlisted key, "
                    "rate-limited per session. Independent of the pane "
                    "terminal below."},
    {"key": "rate_limit_per_minute", "group": "Steering", "type": "int",
     "min": 1, "max": 600,
     "label": "Steering messages per minute",
     "description": "Per-session ceiling on delivered messages. Bounds cost "
                    "and chaos if a client loops. Does not apply to the pane "
                    "terminal, which carries a keystroke stream, not messages."},
    {"key": "max_text_len", "group": "Steering", "type": "int",
     "min": 1, "max": 100000,
     "label": "Max message length",
     "description": "Longest message body accepted for delivery, after "
                    "sanitization."},
    {"key": "allowed_pane_commands", "group": "Steering", "type": "list",
     "label": "Allowed pane commands",
     "description": "A pane only accepts delivery when its foreground process "
                    "is one of these, or one a provider declares for its CLI. "
                    "The guard that stops a message typed at a fallen-back "
                    "shell from executing as a shell command (native builds "
                    "report claude.exe; NVM installs report claude or node; "
                    "kimi reports node). Adds to the providers' commands; "
                    "cannot remove one."},
    {"key": "terminal_enabled", "group": "Pane terminal", "type": "bool",
     "label": "Pane terminal enabled (full shell access)",
     "description": "Serves a real terminal on the trace page over a "
                    "WebSocket. This is UNFILTERED shell access as the user "
                    "running regin serve — no key allowlist, no rate budget, "
                    "no sanitizer, because none of those mean anything against "
                    "a keystroke stream. Admin role is required on top of this "
                    "switch. Note: while a browser terminal is open the pane's "
                    "window follows the BROWSER's size, not yours — tmux gives "
                    "a window one size and resolves it to the latest client; "
                    "it reverts when the tab closes."},
    {"key": "attach_socket", "group": "Pane terminal", "type": "string",
     "label": "Attach socket name",
     "description": "tmux socket for panes regin creates itself, to take over "
                    "a conversation the CLI holds off-pane. Its own server, so "
                    "it works when you are not running tmux and your "
                    "kill-server does not take regin's panes with it."},
]


def _agent_bridge_warnings(current) -> list[str]:
    warnings = []
    if current.terminal_enabled:
        warnings.append(
            "The pane terminal is on: any admin can open a full shell as the "
            "user running `regin serve`.")
    if current.enabled and not current.token:
        warnings.append(
            "Message steering is enabled with no token set, so the headless "
            "/api/bridge/* routes refuse every call. Set agent_bridge.token in "
            "config/settings.local.json.")
    return warnings


_TASKS_FIELDS = [
    {"key": "capture_enabled", "group": "Capture", "type": "bool",
     "label": "Task capture enabled",
     "description": "Arms POST /api/tasks/capture, the one route "
                    "authenticated by an API key instead of the web-UI login. "
                    "While it is off the route 404s for every key. A captured "
                    "task always lands as an idea and can carry no execution "
                    "parameter, so a leaked capture key adds backlog rows and "
                    "nothing else."},
    {"key": "capture_rate_limit_per_minute", "group": "Capture", "type": "int",
     "min": 1, "max": 600,
     "label": "Captures per minute",
     "description": "Per-key ceiling on accepted captures. Bounds the damage "
                    "a looping integration can do to the backlog."},
    {"key": "output_language", "group": "Runs", "type": "string",
     "label": "Output language",
     "description": "The language every task run writes its human-facing "
                    "text in (replies, messages, specs, the finish report). "
                    "A project's own language wins over this. Empty = say "
                    "nothing. At most 64 characters."},
]


def _tasks_warnings(current) -> list[str]:
    if not current.capture_enabled:
        return []
    from lib import api_keys
    if any(not k["revoked"] for k in api_keys.list_keys()):
        return []
    return ["Task capture is on but no live API key exists, so every capture "
            "is refused. Mint one under API keys."]


_TASK_INTEGRATIONS_FIELDS: list[dict] = [
    {"key": "linear_enabled", "group": "Linear", "type": "bool",
     "label": "Enabled",
     "description": "Mute switch for this tracker — off means Linear claims no "
                    "pasted reference, without deleting the key below."},
    {"key": "linear_api_key", "group": "Linear", "type": "string",
     "label": "API key",
     "description": "A Linear personal API key (Settings \u2192 Security & "
                    "access \u2192 Personal API keys). Read-only use: regin "
                    "fetches an issue's title, state, assignee and labels and "
                    "writes nothing back. Empty = Linear off. Stored "
                    "machine-local."},
    {"key": "linear_timeout_seconds", "group": "Linear", "type": "float",
     "min": 0, "step": 0.5, "label": "Request timeout (s)",
     "description": "How long one Linear GraphQL call may take before the "
                    "link records the failure and keeps its last snapshot."},
]


def _settings_blocks() -> dict:
    """Registry of round-trippable nested settings blocks. `scope` routes the
    write: agent_messages goes local because webhook_url can hold a secret
    token that must not land in git-tracked settings.json; agent_sdk because
    cli_path is a machine path and a git-tracked `enabled` would arm
    process-spawning for everyone who pulls; agent_bridge because it holds the
    bridge token and a git-tracked terminal switch would hand everyone who
    pulls a shell; tasks because a git-tracked `capture_enabled` would arm the
    key-authenticated capture route for everyone who pulls."""
    from lib.settings import (AgentBridgeConfig, AgentMemoryConfig,
                              AgentMessagesConfig, AgentSdkConfig,
                              TaskIntegrationsConfig, TasksConfig,
                              TopicEvolutionConfig)
    return {
        "agent-memory": {"attr": "agent_memory", "model": AgentMemoryConfig,
                         "fields": _AGENT_MEMORY_FIELDS, "scope": "shared",
                         "label": "Agent memory settings"},
        "agent-messages": {"attr": "agent_messages", "model": AgentMessagesConfig,
                           "fields": _AGENT_MESSAGES_FIELDS, "scope": "local",
                           "label": "Agent message settings"},
        "topic-evolution": {"attr": "topic_evolution",
                            "model": TopicEvolutionConfig,
                            "fields": _TOPIC_EVOLUTION_FIELDS, "scope": "shared",
                            "label": "Topic evolution settings"},
        "agent-sdk": {"attr": "agent_sdk", "model": AgentSdkConfig,
                      "fields": _agent_sdk_fields(), "scope": "local",
                      "warn": _agent_sdk_warnings,
                      "label": "Agent SDK settings"},
        "agent-bridge": {"attr": "agent_bridge", "model": AgentBridgeConfig,
                         "fields": _AGENT_BRIDGE_FIELDS, "scope": "local",
                         "warn": _agent_bridge_warnings,
                         "label": "Agent bridge settings"},
        "tasks": {"attr": "tasks", "model": TasksConfig,
                  "fields": _TASKS_FIELDS, "scope": "local",
                  "warn": _tasks_warnings,
                  "label": "Task capture settings"},
        "task-integrations": {"attr": "task_integrations",
                              "model": TaskIntegrationsConfig,
                              "fields": _TASK_INTEGRATIONS_FIELDS,
                              "scope": "local",
                              "label": "Task integration settings"},
    }
