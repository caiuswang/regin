"""Frozen dataclasses describing parsed transcript output.

Returned by `lib.trace.transcript_usage.read_usage`. Kept separate from
the parser so consumers can type-annotate without pulling in the parsing
machinery.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def has_encrypted_thinking(thinking_blocks: int, thinking_text: str | None) -> bool:
    """True when a turn carried extended-thinking blocks whose plaintext
    was not captured — the API returned only an encrypted `signature`,
    so the reasoning's token cost is recoverable only by subtraction
    from the turn's API-reported `output_tokens`.

    This is the single predicate that couples two attribution sites which
    must agree on the per-turn token split:

      * `transcript_usage._should_skip_redistribution` — when True, the
        thinking residual must NOT be smeared onto tool_use estimates, so
        tool spans keep their raw `output_token_estimate`.
      * `span_posters._compute_thinking_output` — when True, the
        `assistant.thinking` span claims that residual as
        `max(0, output − text_est − Σ raw non-server tool_use)`, which is
        only correct *because* the tools above were left raw.

    Editing one site without the other silently desyncs the split (double
    counting or a vanished residual), so both gate on this function.
    """
    return thinking_blocks > 0 and not thinking_text


@dataclass(frozen=True)
class TurnUsage:
    model: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    # Turn provenance — used by hook_manager.handlers.turn_trace to
    # emit a stable, idempotent per-turn span (the transcript writes
    # these fields at the top level of each assistant entry).
    uuid: str | None = None
    timestamp: str | None = None
    request_id: str | None = None
    # Assistant response text (text-blocks only) and truncation flag.
    # Used by `assistant_response` spans in the trace dashboard.
    text: str | None = None
    text_truncated: bool = False
    # Extended-thinking reasoning text (thinking-blocks only) and
    # truncation flag. `thinking_blocks` is the count of thinking
    # content blocks observed across all entries sharing the dedup
    # key (proves thinking happened even when the text is redacted).
    # `thinking_signature_bytes` is the sum of every thinking block's
    # encrypted signature length — non-zero whenever thinking
    # happened, useful as a presence signal when `thinking_text` is
    # empty due to redaction.
    thinking_text: str | None = None
    thinking_text_truncated: bool = False
    thinking_blocks: int = 0
    thinking_signature_bytes: int = 0
    # Per-API-call latency in milliseconds — derived locally as
    # (this turn's first-entry timestamp − the prior transcript
    # entry's timestamp). Approximates how long Anthropic took to
    # return this particular response; useful as the per-iteration
    # latency metric the trace UI shows on each assistant_response.
    inference_duration_ms: int | None = None
    # Full prompt-cycle wall-clock from Claude Code's `system:
    # turn_duration` entry (every API call + tools + hooks). Only the
    # final text-emitting response in a multi-iteration turn carries
    # this — intermediate tool-calling rounds get None. Surfaced
    # alongside `inference_duration_ms` so a viewer can see "this
    # specific API call took 3s but the whole prompt cycle took 33s."
    turn_total_duration_ms: int | None = None
    # Owning user-prompt entry's uuid, resolved by walking the
    # transcript's parentUuid chain. Lets `turn_trace` link the
    # response span to its prompt span via deterministic span_id.
    prompt_uuid: str | None = None
    # Tool calls issued during this turn, with their result outcome
    # patched in via tool_use_id correlation. `is_error` is None for
    # in-flight calls (no tool_result observed in this transcript),
    # False on success, True on error.
    tool_calls: tuple[dict, ...] = ()

    @property
    def context_used(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_creation_tokens


@dataclass(frozen=True)
class TranscriptSystemEvent:
    """A Claude Code `type: system` transcript entry worth tracing.

    Carried separately from `TurnUsage` because these are diagnostic
    side-events — Claude Code's own measurement of how long the turn +
    its post-turn hooks took, plus per-hook latency. They tie back to
    a specific assistant turn via `turn_uuid`.
    """

    uuid: str
    parent_uuid: str | None
    timestamp: str | None
    subtype: str
    duration_ms: int | None
    # The assistant entry this event measures. Resolved during the
    # linear scan by following parentUuid back to the most recent
    # `type=assistant` entry.
    turn_uuid: str | None
    payload: dict


@dataclass(frozen=True)
class TranscriptAttachment:
    """A Claude Code `type: attachment` transcript entry worth tracing.

    Most attachment kinds are skipped (`hook_success`,
    `hook_additional_context`) because they're high-volume internal
    bookkeeping. The kinds we keep are the ones that change the
    agent's behavior or surface area:
      * `task_reminder`     — harness nudged the agent about tasks
      * `skill_listing`     — the catalog of available skills was
                              injected (full at session start, deltas
                              afterwards)
      * `deferred_tools_delta` — new tools became available via
                              ToolSearch
    """

    uuid: str
    parent_uuid: str | None
    timestamp: str | None
    kind: str
    payload: dict


@dataclass(frozen=True)
class TranscriptLocalCommand:
    """A Claude Code local command invocation captured in the transcript.

    Two flavours, both handled by Claude Code without sending a prompt to
    the model (so neither fires `UserPromptSubmit` — the transcript scan
    is the only way to surface them):

      * Slash commands (/add-dir, /clear, /exit, /usage, /help, …) —
        detected via `<command-name>` / `<local-command-stdout>` /
        `<local-command-caveat>` tags. They appear either as `type=user`
        (message.content) for user-initiated commands or as
        `type=system, subtype=local_command` (top-level content) for
        system-initiated ones like `/usage`. The entries form a
        `parentUuid` chain: stdout.parent_uuid → command-name.uuid →
        caveat.uuid (when a caveat is present).
      * Bang/bash commands (`!ls`) — detected via `<bash-input>` and a
        paired `<bash-stdout>…</bash-stdout><bash-stderr>…</bash-stderr>`
        entry (both wrappers live in one `type=user` entry). No caveat,
        no `<command-args>`. `command_name` carries the leading `!`
        (`!ls`) to distinguish it from a slash command in the UI.

    Fields:
      * `command_uuid` — keys the emitted `harness.local_command` span
        (span_id `cmd-<uuid[:13]>`) and is the primary cache entry.
      * `command_name` — the slash text (`/add-dir`) or `!<cmd>` (`!ls`).
      * `args` — raw `<command-args>` payload, may be empty. Always None
        for bash commands.
      * `timestamp` — ISO timestamp of the command-name / bash-input entry.
      * `stdout_uuid` / `stdout_text` — paired stdout entry. Stdout may be
        empty (`/clear`) or missing entirely (rare).
      * `stderr_text` — bash stderr, when present; None for slash commands.
      * `caveat_uuid` — leading `<local-command-caveat>` entry when
        present (user-typed slash commands only; system-emitted and bash
        commands skip it).
    """

    command_uuid: str
    command_name: str
    args: str | None
    timestamp: str | None
    stdout_uuid: str | None
    stdout_text: str | None
    caveat_uuid: str | None
    stderr_text: str | None = None


@dataclass(frozen=True)
class RewindFork:
    """One `/rewind` event recovered from the transcript's parentUuid graph.

    A rewind leaves no explicit marker in the JSONL — it surfaces as a
    *fork*: the user rewound to an earlier message and submitted a new
    prompt, so the discarded turns and the new (live) turn share the same
    `parentUuid`. The discarded branch is never deleted; it stays in the
    file, orphaned (nothing on the live-leaf chain descends from it).

    `fork_uuid` is the live node both branches hang off; `orphan_root` is
    the first message of the discarded branch; `orphan_uuids` is its whole
    subtree. The spans built from those uuids are flagged `rewound_away`
    (deterministic ids: `prompt-`/`resp-`/`think-`/`cmd-<uuid[:13]>`, tool
    spans via `turn_uuid`), and one synthetic `rewind` boundary span
    (`span_id=rewind-<fork_uuid[:13]>`) anchors the collapsed branch in the
    UI. `rolled_back_files` (populated from `file-history-snapshot` rows
    when the rewind reverted code) lists the paths whose edits were undone,
    with `@vN` refs for an on-demand before/after diff.

    See `lib.trace.rewind_detect.detect_rewinds` (conversation fork) and
    `lib.trace.file_history` (code rollback).
    """

    fork_uuid: str
    orphan_root: str
    orphan_uuids: frozenset[str]
    abandoned_prompt_uuids: tuple[str, ...]
    live_child_uuid: str | None
    fork_timestamp: str | None
    # {path, before_ref, after_ref} per file whose edits the rewind undid.
    # Empty for a conversation-only rewind. Refs are `<pathhash>@vN`
    # backup names; content is read lazily (never inlined here).
    rolled_back_files: tuple[dict, ...] = ()

    @property
    def span_id(self) -> str:
        """Deterministic id of this fork's `rewind` boundary span.

        Keyed on `orphan_root` (unique per discarded branch — each node has
        exactly one parent), NOT `fork_uuid`: rewinding twice to the same
        node yields two discarded branches sharing a `fork_uuid`, and each
        needs its own marker span."""
        return f'rewind-{self.orphan_root[:13]}'


@dataclass(frozen=True)
class TranscriptUsage:
    turns: list[TurnUsage]
    model: str | None  # model on the most recent assistant turn
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    peak_context_tokens: int
    attachments: tuple[TranscriptAttachment, ...] = ()
    system_events: tuple[TranscriptSystemEvent, ...] = ()
    local_commands: tuple[TranscriptLocalCommand, ...] = ()
    # uuid → submitted text for the user-prompt entries that triggered a
    # turn (i.e. each turn's `prompt_uuid`). Keyed so turn_trace can emit
    # the turn-anchor `prompt-<uuid>` span deterministically — uuid, not
    # text, is what distinguishes a real prompt anchor from a client-only
    # slash command (`/workflows`) the live hook mis-keyed. Only the
    # prompt_uuids actually referenced by a turn are kept, so this stays
    # small even on long sessions.
    prompt_texts: dict[str, str] = field(default_factory=dict)
    # uuid → ISO timestamp of the same prompt entries, so the re-emitted
    # anchor sits at prompt time on the timeline (not re-emission time).
    prompt_timestamps: dict[str, str] = field(default_factory=dict)
    # uuid → inline base64 `image` parts (`{idx, media_type, data_b64}`)
    # carried in that prompt entry's `message.content`. The durable
    # fallback turn_trace uses to attach `prompt_images` to the correct
    # anchor when Claude Code's per-session image cache is already gone
    # (e.g. post-session repair). Same anchor-only keying as prompt_texts.
    prompt_image_parts: dict[str, list] = field(default_factory=dict)
    # tool_use id → the uuid of the assistant turn that issued it, derived
    # from the transcript's parentUuid graph. Lets the live tool-span
    # parent backfill (turn_trace) set `parent_id = resp-<turn_uuid>`
    # deterministically without re-deriving the issuing turn at write time.
    tool_use_to_turn_uuid: dict[str, str] = field(default_factory=dict)
    # `/rewind` events recovered from the parentUuid graph (see RewindFork).
    # Empty on the overwhelming majority of sessions that never rewind.
    rewinds: tuple[RewindFork, ...] = ()
    # Tool calls a provider denied *outside* regin and recorded only in its
    # transcript (Kimi resolves permissions in its own TUI, then logs a
    # `permission.record_approval_result`). A denied call fires no PostToolUse,
    # so its PENDING tool span is never resolved and the serve-time merge drops
    # it — turn_trace re-materialises it as a `tool.<name>` deny span from these.
    # Each: {tool_use_id, tool_name, denial_reason, timestamp}. Empty for Claude
    # (its denials arrive live via the PermissionDenied hook).
    permission_denials: tuple[dict, ...] = ()
