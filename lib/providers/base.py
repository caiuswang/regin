"""Provider contracts for agent-specific integration points.

regin currently defaults to Claude paths/schemas, but the core should
resolve those via a provider adapter so Codex/other agents can plug in
without hard-coded path rewrites across the codebase.
"""

from __future__ import annotations

import glob
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict

if TYPE_CHECKING:
    from hook_manager.core import HookPayload, HookResponse, PermissionRequestInfo


@dataclass(frozen=True)
class ProviderCapabilities:
    """Feature support matrix surfaced to CLI/UI callers."""

    skills: bool = False
    hooks: bool = False
    sessions: bool = False
    transcript_usage: bool = False


@dataclass(frozen=True)
class SkillCatalogLayout:
    """`skill_dirs` hold `<name>/SKILL.md`; `command_dirs` hold `<name>.md`;
    `plugins` reads Claude Code's enabled-plugin registry."""

    skill_dirs: tuple[tuple[str, ...], ...]
    command_dirs: tuple[tuple[str, ...], ...] = ()
    plugins: bool = False


class LaunchCapabilities(TypedDict):
    """How a task run may be launched through one provider.

    `launch_modes` is the provider's own meaning of each value: `sdk` is its
    structured channel (claude_agent_sdk, kimi's ACP), `tmux` a pane. Empty
    means the provider cannot launch a task run at all.
    """

    launch_modes: list[Literal["sdk", "tmux"]]
    fields: list[Literal["model", "permission_mode", "effort"]]
    binary: str
    pane_commands: list[str]
    labels: dict[str, str]


class AgentProvider:
    """Base adapter for provider-specific paths and conventions."""

    provider_id: str = "unknown"
    display_name: str = "Unknown"
    capabilities: ProviderCapabilities = ProviderCapabilities()
    # Storage format for hook_manager install/uninstall. "json" providers
    # (Claude/Codex) keep their hooks in a settings.json `hooks` map; "toml"
    # providers (Kimi) keep them in a config.toml `[[hooks]]` array. The hooks
    # blueprint branches on this to pick the right reader/writer.
    hook_config_format: str = "json"
    # Wire format for a hook handler's *stdout response*. "claude" providers
    # (Claude/Codex) speak the full Claude Code hook-output JSON
    # (suppressOutput / systemMessage / the hookSpecificOutput envelope, …).
    # "kimi" providers speak Kimi Code's much smaller surface: only
    # ``hookSpecificOutput.permissionDecision``[/Reason] is parsed as JSON and
    # every other stdout byte is shown verbatim / appended to context. The
    # hook_manager runner branches on this so we never print Claude-only fields
    # into a Kimi session (where `{"suppressOutput": true}` would render raw).
    hook_output_format: str = "claude"
    # On-disk transcript schema tag. The parser itself is `parse_transcript`
    # (a provider method); turn_trace only uses this to gate the Claude-shaped
    # enrichment that has no Kimi analogue (session-title span + the cheap
    # tail-read of the latest model).
    transcript_format: str = "claude"
    # Whether a per-turn `Stop` event should synthesize the session-end marker
    # for this provider. True only for CLIs that never emit `SessionEnd` in
    # real runs (Codex); the Stop-fallback handler reads this off the session's
    # provider so it never ends a Claude/Kimi session that Stops per turn.
    synthesizes_session_end_from_stop: bool = False
    # Environment variables this CLI exports into every child process holding
    # the live session id, most-authoritative first. Empty for harnesses that
    # export nothing — those resolve through `REGIN_SESSION_ID` or the trace
    # fallback instead (see `lib/session_probe.py`). Read off the class, so
    # declaring one must not require constructing the provider.
    session_id_env_vars: tuple[str, ...] = ()

    def hook_events(self) -> tuple[str, ...] | None:
        """Supported hook event names for install wiring.

        Returns None to indicate "use the full spec registry".
        """
        return None

    def resolve_transcript_path(self, payload: "HookPayload") -> str | None:
        """Locate this session's transcript file for the given hook payload.

        The default reads the `transcript_path` Claude/Codex put on the hook
        payload. Providers whose CLI does not pass a path (Kimi) override this
        to locate the file from the session id.
        """
        path = (payload.raw or {}).get("transcript_path")
        return path if isinstance(path, str) and path else None

    def find_session_transcript(self, session_id: str) -> str | None:
        """Locate a session's transcript from its id alone, with no hook payload.

        Server-side backfill/self-heal paths (live rescan, span repair, the
        serve-time prompt-expansion read) only ever hold a trace_id, so they
        cannot go through `resolve_transcript_path`. The default matches the
        Claude/Codex layout — a single ``<session_id>.jsonl`` under some
        project subdirectory of `transcript_projects_dir()`. Providers whose
        session is a directory rather than a file (Kimi) override this.
        """
        if not session_id:
            return None
        try:
            base = self.transcript_projects_dir()
        except NotImplementedError:
            return None
        matches = glob.glob(str(Path(base) / '*' / f'{session_id}.jsonl'))
        return matches[0] if matches else None

    def live_sessions(self) -> dict[str, str] | None:
        """`{session_id: handle}` for every session this CLI still holds.

        The handle is whatever name the CLI's own "open this one" command
        takes, and it is `''` when there is none — regin uses it to take a
        conversation over, so a session that cannot be opened must be
        distinguishable from one that can rather than guessed at.

        None means the CLI could not be asked; see `live_session_ids`.
        """
        return None

    def launch_capabilities(self) -> LaunchCapabilities:
        """What a task run launched through this provider may carry.

        The default declares no launch channel, so arming a provider that has
        not opted in is refused rather than quietly launched as Claude.
        """
        return {"launch_modes": [], "fields": [], "binary": "",
                "pane_commands": [], "labels": {}}

    def channel_launcher(self):
        """Callable launching a task run through this provider's structured
        channel, or None when the provider launches Claude-style (the
        sdk/tmux arms).

        A channel launcher matches `supervisor.launch_run`'s contract: it
        takes the prompt plus the launch-parameter subset and returns a
        handle with `trace_id` and `add_done_callback`. Providers whose CLI
        speaks a structured wire protocol (Kimi's ACP) override this; the
        import stays inside the override so the channel module loads only
        when the provider is actually asked to launch.
        """
        return None

    def live_session_ids(self) -> frozenset[str] | None:
        """Session ids this CLI reports as running right now, or None if it
        cannot say.

        The tmux pane registry answers a narrower question — which session owns
        a pane — and callers have been reading it as "which session is alive".
        Those diverge for any conversation the CLI hosts off-pane: Claude
        Code's background sessions run under its own daemon on a private PTY,
        so they own no pane and are indistinguishable there from a session that
        exited. Asking the CLI closes that gap for both kinds at once.

        None, not an empty set, is the answer when the CLI is absent or
        refuses: a caller must be able to tell "nothing is running" from
        "nobody could be asked", or a probe that fails reads as a session that
        died.
        """
        rows = self.live_sessions()
        return None if rows is None else frozenset(rows)

    def subagent_transcript_paths(self, main_path: str) -> list[str]:
        """Every subagent transcript belonging to the session at `main_path`.

        The live rescan stats these for freshness: a subagent streaming while
        the main transcript sits still (the parent blocked on an Agent tool) is
        the one case where the main file alone says "nothing happened". The
        default is Claude's `<session>/subagents/agent-*.jsonl` layout;
        providers that write a subagent elsewhere (Kimi: a sibling `agent-N/`
        directory) override this.
        """
        from pathlib import Path
        return glob.glob(
            str(Path(main_path).with_suffix('') / 'subagents' / 'agent-*.jsonl'))

    def parse_transcript(self, transcript_path: str, *, max_text_bytes: int | None = None):
        """Parse a transcript file into a `lib.trace.transcript_models.TranscriptUsage`.

        The default handles the Claude/Codex message-per-line JSONL. Providers
        with a different on-disk format (Kimi's event-sourced wire.jsonl)
        override this; every parser returns the same dataclass so the span/
        usage posters stay format-agnostic.
        """
        from lib.trace.transcript_usage import read_usage
        return read_usage(transcript_path, max_text_bytes=max_text_bytes)

    def permission_request_events(self) -> tuple[str, ...]:
        """Events that can carry permission request details for this provider."""
        return ("PermissionRequest",)

    def build_permission_request_info(self, payload: "HookPayload") -> "PermissionRequestInfo | None":
        """Normalize provider-specific permission request payloads.

        Providers that do not support a permission prompt on the incoming event
        should return None.
        """
        return None

    def serialize_permission_decision(
        self,
        info: "PermissionRequestInfo",
        selected_option_id: str | None = None,
    ) -> "HookResponse":
        """Convert a selected permission option into provider hook output."""
        from hook_manager.core import HookResponse
        return HookResponse()

    def permission_awaits_human(self, payload: "HookPayload") -> bool:
        """Whether a permission request genuinely stops to ask a *person*.

        A `permission.request` is only a **blocker** worth surfacing to the
        inbox / push channels when the harness will actually wait on a human
        decision. Some permission modes auto-resolve the request (grant or
        deny) with no human in the loop — recording those as a blocker is
        pure noise (the complaint that motivated this hook).

        It cannot be read off the mode string alone: under Claude's
        `acceptEdits`, an `Edit` is auto-accepted but a `Bash` still prompts —
        the answer is a function of *mode and tool together*, which only the
        provider knows. Default here is conservative: assume a human is
        needed. A provider that knows its own auto-grant semantics overrides
        to narrow it; over-notifying beats silently swallowing a real
        blocker."""
        return True

    def tool_failure_error_text(self, raw_error: object) -> str:
        """Normalize a `PostToolUseFailure` payload's `error` field to a plain
        display string.

        Claude/Codex put a bare string here, so the default just strips it.
        Providers whose CLI emits a structured error object (Kimi's
        ``{code, message, retryable}``) override this to pull the human
        message out — keeping the per-provider shape inside the adapter
        instead of an ``isinstance`` branch in the shared failure handler.
        """
        return raw_error.strip() if isinstance(raw_error, str) else ''

    def normalize_tool_response(
        self, tool_name: str, tool_input: dict, tool_response: dict
    ) -> dict:
        """Reshape a provider's `PostToolUse` result into the Claude-shaped
        `tool_response` the shared per-tool span builders expect.

        regin's `post_tool_trace` builders are written against Claude's keys
        (`stdout`/`stderr` for Bash, `file.content` for Read, …). Claude/Codex
        already send those, so the default is a pass-through. A provider whose
        CLI returns a different result shape (Kimi wraps every tool result in a
        single ``{output, isError}`` text envelope) overrides this to map the
        envelope onto the keys the builders read — keeping the per-provider
        shape inside the adapter instead of an ``isinstance`` branch per
        builder.
        """
        return tool_response

    def client_version(self) -> str | None:
        """Installed CLI version for this provider, used to fingerprint payload
        schema drift. Returns None until a provider wires a version probe — the
        drift store dispatches here so adding a probe is just an override, not
        another ``if agent == ...`` branch in shared code.
        """
        return None

    def reconcile_subagents(self, session_id: str, *, live: bool = False) -> None:
        """Re-nest a subagent's flat tool/turn spans under its subagent trace.

        Most CLIs (Claude) emit a subagent's tool calls against the subagent's
        own session, so nothing is needed and the default is a no-op. A
        provider whose CLI fires sub-tool hooks under the PARENT session_id
        (Kimi) overrides this to trigger the server-side reconciler — keeping
        that provider-specific quirk in the adapter instead of an
        ``if provider_id == 'kimi'`` branch in the shared SubagentStop handler.

        ``live=True`` is set by callers that can fire while a subagent is still
        running (the trace view's rescan poll), so the reconciler must not
        record an end for it.
        """
        return None

    def tool_failure_is_user_rejection(self, raw_error: object) -> bool:
        """Whether a `PostToolUseFailure` actually represents the user
        *rejecting* a permission prompt rather than a genuine tool error.

        Most providers surface a denial through a dedicated path (Claude's
        `PermissionDenied` hook), so the default is False. A provider that
        funnels rejections through the failure event (Kimi) overrides this so
        the shared handler can stay silent — the denial is already captured as
        the provider's transcript deny span, and a `tool.failure` on top of it
        would double-render the one rejected call.
        """
        return False

    def prepare_workspace(self, cwd: Path) -> None:
        """Make `cwd` launchable unattended before a run starts there."""
        return None

    def skill_invocation(self, name: str, args: str, also: list[str]) -> str:
        """The lines an armed skill puts in front of a task run's prompt.

        A slash command is what the CLI resolves from the *start* of a prompt,
        so the invocation is the first line and the body follows as the rest
        of the skill's arguments. The CLI expands only that one command, so
        further armed skills are named on the next line for the agent to load
        itself.
        """
        line = f"/{name}"
        if args:
            line += f" {args}"
        line += "\n\n"
        if also:
            named = ", ".join(f"`{n}`" for n in also)
            line += (f"Also load and follow these skills with the Skill tool: "
                     f"{named}.\n\n")
        return line

    def skill_catalog_layout(self) -> "SkillCatalogLayout":
        """Where this CLI resolves skills from, relative to each project
        directory and to home."""
        return SkillCatalogLayout(skill_dirs=(self.project_skills_subpath(),))

    def project_config_dir(self) -> str:
        """The per-project config directory a fresh worktree must be given."""
        return self.project_skills_subpath()[0]

    def global_skills_dir(self) -> Path:
        raise NotImplementedError

    def project_skills_subpath(self) -> tuple[str, ...]:
        """Relative subpath used for per-project deployment targets."""
        return (".claude", "skills")

    def skill_invoke_path(self, skill_id: str) -> str:
        """Synthetic trace path for explicit slash-command invocation."""
        raise NotImplementedError

    def skill_launch_path(self, skill_id: str) -> str:
        """Synthetic trace path for assistant-initiated Skill tool launches."""
        raise NotImplementedError

    def skill_content_relpath(self, skill_id: str) -> str:
        """Relative path under $HOME for deployed skill content.md."""
        raise NotImplementedError

    def skill_id_from_read_path(self, file_path: str, *, home: str | None = None) -> str | None:
        """Return skill id when the read path points to content.md."""
        raise NotImplementedError

    def plans_dir(self) -> Path:
        raise NotImplementedError

    def traces_dir(self) -> Path:
        raise NotImplementedError

    def hook_settings_path(self) -> Path:
        raise NotImplementedError

    def hook_manager_config_path(self) -> Path:
        raise NotImplementedError

    def hook_payload_log_path(self) -> Path:
        raise NotImplementedError

    def transcript_projects_dir(self) -> Path:
        raise NotImplementedError


def session_agent_type(trace_id: str) -> str | None:
    """The `sessions.agent_type` recorded for one trace, or None."""
    if not trace_id:
        return None
    try:
        from sqlmodel import select
        from lib.orm import SessionLocal
        from lib.orm.models.trace import Session as SessionModel
        with SessionLocal() as db:
            return db.exec(
                select(SessionModel.agent_type)
                .where(SessionModel.trace_id == trace_id)
            ).first()
    except Exception:
        return None


def provider_for_agent_type(agent_type: str | None,
                            default_provider_id: str = "claude") -> AgentProvider:
    """Provider adapter implied by one session's stored `agent_type`.

    Only an agent_type that maps to a registered, non-generic provider
    redirects. `agent_type` is free-form vendor text and is frequently NULL or
    a Claude subagent role ('explorer', 'verifier', …); those sessions really
    do live in `default_provider_id`'s layout, so treating an unrecognized
    value as 'generic' would break transcript discovery for most of the store.
    """
    from lib.providers.registry import build_provider, canonical_agent_kind, is_provider_id
    kind = canonical_agent_kind(agent_type)
    if kind and kind != "generic" and is_provider_id(kind):
        return build_provider(kind)
    return build_provider(default_provider_id)


def provider_for_trace(trace_id: str, default_provider_id: str = "claude") -> AgentProvider:
    """Provider adapter for ONE session, resolved from its stored agent_type.

    Backfill / self-heal paths run inside the server long after the hook that
    recorded the session, so `settings.active_provider` says nothing about
    which CLI actually wrote that trace's transcript — a single global would
    send every Kimi session down Claude's path layout (and back).
    """
    return provider_for_agent_type(session_agent_type(trace_id), default_provider_id)
