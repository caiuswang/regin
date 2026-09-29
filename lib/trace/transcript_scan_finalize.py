"""End-of-pass resolution for the transcript scan: prompt anchors, `/rewind`
forks, interrupted tool calls and local-command assembly."""

from __future__ import annotations

from dataclasses import replace

from lib.trace.transcript_models import (
    RewindFork,
    TranscriptLocalCommand,
    TranscriptUsage,
    TurnUsage,
)
from lib.trace.file_history import (
    parse_snapshot_rows,
    rolled_back_files,
    version_map_for,
)
from lib.trace.rewind_detect import detect_rewinds
from lib.trace.transcript_parsers import _walk_to_prompt
from lib.trace.transcript_scan_helpers import (
    _TurnBuilder,
    _builder_to_turn_usage,
    _has_tokens,
)


class _FinalizeScanMixin:
    """Finalize-time resolution of `_TranscriptScan` state into a `TranscriptUsage`."""

    # ---- finalize ------------------------------------------------------------

    def _counted_builders(self) -> list[_TurnBuilder]:
        # A turn only counts toward token totals + peak context if it
        # actually carried a `usage` block — entries with content but
        # no usage are subordinate fragments, not standalone turns.
        # A failed API call is the exception: it is billed nothing, so its
        # all-zero usage adds nothing here, but it must survive as a turn or
        # the prompt it answers loses its only anchor.
        return [
            b for b in (self.builders[k] for k in self.order)
            if _has_tokens(b) or b.is_api_error
        ]

    def _has_emit_worthy_rows(self, counted: list[_TurnBuilder]) -> bool:
        # We still publish a usage object when only attachments /
        # system events / local-command rows were found so turn_trace
        # can emit the corresponding harness.* spans on a session that
        # hasn't yet had an assistant turn (e.g. /add-dir before the
        # first real prompt).
        return bool(
            counted or self.attachments or self.system_events or self.lc_commands
        )

    def _resolve_prompt_anchors(self) -> tuple[set[str], dict[str, str], dict[str, str]]:
        """Set each builder's `prompt_uuid` by walking the parentUuid
        chain from its (first) assistant entry back to the nearest
        real-prompt ancestor. Replaces the chronological "last user
        entry" heuristic with the transcript's own ground-truth tree, so
        a turn anchors to the prompt that opened it even when synthetic
        user entries (carriers, tool_results, task-notifications) sit
        between the prompt and the response. Unresolvable turns (chain
        reaches root through meta-only ancestors) keep `prompt_uuid=None`.

        Pure w.r.t. the accumulator's command/prompt *sets*: the live
        resumable rescan calls `finalize` on every poll, so destructively
        narrowing `command_prompt_uuids` or growing `real_prompt_uuids`
        here would corrupt resolution on the next poll. Instead this
        returns `(command_anchor_uuids, promoted_texts, command_expansions)`
        for finalize to thread through. `builder.prompt_uuid` is recomputed
        from scratch each call, so overwriting it is safe — it is derived,
        not accumulated."""
        # Gate command candidates to those with an isMeta expansion child —
        # the skill-command signature. Display/mode commands and
        # workflow-resume nudges lack it, so they stay local-command-only.
        #
        # A command that prints a `<local-command-stdout>` line (e.g. /goal →
        # "Goal set: …") puts that stdout entry between its echo and the
        # expansion, so the isMeta child's parent is the stdout, not the
        # command. Bridge that gap: map each command's stdout uuid back to the
        # command, so an expansion under the stdout counts as one under the
        # command. (Bash `!cmd` stdout parents aren't command candidates, so
        # the `in command_prompt_uuids` filter leaves them out.)
        stdout_to_command = {
            info['uuid']: cmd_uuid
            for cmd_uuid, info in self.lc_stdout_by_parent.items()
            if cmd_uuid in self.command_prompt_uuids
        }
        meta_parents = set(self.meta_expansion_parents)
        for muuid in self.meta_expansion_parents:
            bridged = stdout_to_command.get(muuid)
            if bridged is not None:
                meta_parents.add(bridged)
        command_anchor_uuids = self.command_prompt_uuids & meta_parents
        # Two passes, because the task-notification fallback needs to know
        # which commands actually opened a turn (see `_resolve_anchor`).
        # Pass 1 can only land on a command anchor by NOT crossing a
        # notification, so its command hits are exactly that set.
        self._assign_anchors(command_anchor_uuids, frozenset())
        self._assign_anchors(command_anchor_uuids, frozenset(
            b.prompt_uuid for b in self.builders.values()
            if b.prompt_uuid in command_anchor_uuids
        ))
        return (
            command_anchor_uuids,
            self._promoted_anchor_texts(command_anchor_uuids),
            self._command_expansions(command_anchor_uuids, stdout_to_command),
        )

    def _assign_anchors(
        self, command_anchor_uuids: set[str], turn_opening_commands: frozenset,
    ) -> None:
        """Recompute every builder's `prompt_uuid`. Derived, not accumulated,
        so re-running it is safe (see `_resolve_prompt_anchors`)."""
        for builder in self.builders.values():
            if builder.uuid is None:
                continue
            builder.prompt_uuid = self._resolve_anchor(
                builder.uuid, command_anchor_uuids, turn_opening_commands,
            )

    def _resolve_anchor(
        self, start: str | None, command_anchor_uuids: set[str],
        turn_opening_commands: frozenset = frozenset(),
    ) -> str | None:
        """Walk the parentUuid chain from a turn's assistant to its opener.

        Stops at the nearest typed prompt OR slash-command candidate — so a
        turn opened by `/review` anchors on the command (it sits between the
        response and the prior typed prompt), fixing the off-by-one where it
        used to walk past to the previous prompt.

        A `<task-notification>` is a turn boundary, because a command the
        chain reaches ONLY by crossing one never opened a turn of its own —
        `/workflows`, typed to watch a run that some earlier prompt launched,
        would otherwise capture that run's completion turns. So on crossing
        one, only typed prompts and `turn_opening_commands` (commands that
        anchored a turn without crossing a notification) stay eligible.

        Gating on "did this command ever open a turn" rather than on the
        boundary alone is what keeps a long agentic command-turn intact: a
        subagent finishing mid-turn posts a task-notification, and every
        response after it used to walk past its own `/command` to whatever
        the user typed before — stranding the rest of the turn, its final
        answer included, under a different prompt. Cycle-guarded via a
        visited set."""
        cursor = start
        seen: set[str] = set()
        while cursor is not None and cursor not in seen:
            if cursor in self.real_prompt_uuids:
                return cursor
            if cursor in command_anchor_uuids:
                return cursor
            if cursor in self.task_notification_uuids:
                return _walk_to_prompt(
                    self.entry_parent.get(cursor),
                    self.entry_parent,
                    self.real_prompt_uuids | turn_opening_commands,
                )
            seen.add(cursor)
            cursor = self.entry_parent.get(cursor)
        return None

    def _promoted_anchor_texts(
        self, command_anchor_uuids: set[str],
    ) -> dict[str, str]:
        """A skill command that resolved to a turn anchor becomes a
        first-class prompt: it needs anchor text so a `prompt-<uuid>` span is
        emitted (turn header + sidebar entry). Its `harness.local_command`
        span is already suppressed at finalize for the whole `command_anchor_
        uuids` set, so the turn renders one /review card.

        Returns the uuid → command-text mapping for anchors not already in
        `prompt_texts` (the prior in-place `setdefault` semantics), without
        mutating the accumulator — finalize merges this into the anchor-text
        lookup. Adding to `real_prompt_uuids` (the old behaviour) was a no-op
        in a single pass and a resolution-corrupting leak across resumes, so
        it is dropped."""
        out: dict[str, str] = {}
        for builder in self.builders.values():
            pu = builder.prompt_uuid
            if pu and pu in command_anchor_uuids and pu not in self.prompt_texts:
                out[pu] = self.command_prompt_texts[pu]
        return out

    def _command_expansions(
        self, command_anchor_uuids: set[str], stdout_to_command: dict[str, str],
    ) -> dict[str, str]:
        """uuid → expanded prompt text for anchored slash commands.

        Maps the expansion texts (captured from isMeta child entries) to their
        command uuids, handling the /goal edge case where the expansion's parent
        is a stdout entry (not directly the command). Returns only expansions
        for anchors that actually resolved to a turn (same uuid filtering as
        `_promoted_anchor_texts`), so they stay synchronized."""
        out: dict[str, str] = {}
        for expansion_parent_uuid, expansion_text in self.meta_expansion_texts.items():
            # The expansion's parent is either the command directly, or a
            # stdout entry whose parent is the command (the /goal bridge case).
            command_uuid = stdout_to_command.get(expansion_parent_uuid, expansion_parent_uuid)
            if command_uuid in command_anchor_uuids:
                out[command_uuid] = expansion_text
        return out

    def _tool_use_to_turn_uuid(self) -> dict[str, str]:
        """tool_use id → issuing assistant turn uuid, for write-time tool
        parentage (Phase 2: `parent_id = resp-<turn_uuid>`)."""
        return {
            tu_id: builder.uuid
            for tu_id, builder in self.tool_use_to_turn.items()
            if builder.uuid
        }

    def _live_ancestors(self, uuid: str | None) -> list[str]:
        """The fork node and every live-branch ancestor up to the root —
        i.e. the file state that existed *before* the rewound edits, used as
        the rollback baseline. Cycle-guarded."""
        out: list[str] = []
        seen: set[str] = set()
        cur = uuid
        while cur is not None and cur not in seen:
            seen.add(cur)
            out.append(cur)
            cur = self.entry_parent.get(cur)
        return out

    def _compute_rewinds(self) -> tuple[RewindFork, ...]:
        """Detect `/rewind` forks and enrich each with the files its rewind
        reverted (when it rolled back code). Empty on the vast majority of
        sessions that never rewind."""
        forks = detect_rewinds(
            self.entry_parent,
            self.entry_kind,
            self.real_prompt_uuids,
            [self.last_prompt_leaf, self.file_tail_uuid],
            entry_ts=self.entry_ts,
        )
        if not forks:
            return ()
        snapshots = parse_snapshot_rows(self.snapshot_rows)
        if not snapshots:
            return tuple(forks)
        order = {u: i for i, u in enumerate(self.entry_parent)}
        enriched: list[RewindFork] = []
        for fork in forks:
            orphan_ordered = sorted(
                fork.orphan_uuids, key=lambda u: order.get(u, 0),
            )
            abandoned = version_map_for(orphan_ordered, snapshots)
            baseline = version_map_for(
                self._live_ancestors(fork.fork_uuid), snapshots,
            )
            files = rolled_back_files(abandoned, baseline)
            enriched.append(
                replace(fork, rolled_back_files=tuple(files)) if files else fork
            )
        return tuple(enriched)

    def _flag_interrupted_tool_calls(self) -> None:
        """Mark the unresolved tool_calls of each user-interrupted assistant
        turn so `span_posters._emit_deny_and_error_spans` synthesizes a
        flagged `tool.<name>` span for them.

        Only calls with no paired tool_result (`is_error` is None) and that
        aren't server-side are flagged: a call that completed before the
        interrupt already carries its own resolved span, and the is_error
        gate also keeps us off the tool_result-delivered interrupt variant
        (handled live by `tool.failure`). The input is copied onto the call
        (same path the deny synth uses) so the span can show the command /
        file that was cancelled."""
        for mid in self.interrupted_message_ids:
            builder = self.builders.get(mid)
            if builder is None:
                continue
            for call in builder.tool_calls:
                if call.get('is_error') is not None or call.get('server_side'):
                    continue
                call['interrupted'] = True
                tu_id = call.get('id')
                if isinstance(tu_id, str) and tu_id:
                    self._capture_deny_input(call, tu_id)

    def finalize(self, *, max_text_bytes: int | None) -> TranscriptUsage | None:
        self._flag_interrupted_tool_calls()
        counted = self._counted_builders()
        if not self._has_emit_worthy_rows(counted):
            return None
        command_anchor_uuids, promoted_texts, command_expansions = self._resolve_prompt_anchors()
        finalized = [_builder_to_turn_usage(b, max_text_bytes) for b in counted]
        anchor_texts = self._anchor_prompt_texts(finalized, promoted_texts)
        anchor_ts, anchor_images = self._anchor_side_tables(anchor_texts)
        anchor_prompt_ids = {
            u: self.prompt_ids[u]
            for u in anchor_texts
            if u in self.prompt_ids
        }
        return TranscriptUsage(
            turns=finalized,
            model=self.latest_model,
            input_tokens=sum(t.input_tokens for t in finalized),
            output_tokens=sum(t.output_tokens for t in finalized),
            cache_read_tokens=sum(t.cache_read_tokens for t in finalized),
            cache_creation_tokens=sum(t.cache_creation_tokens for t in finalized),
            peak_context_tokens=max((t.context_used for t in finalized), default=0),
            attachments=tuple(self.attachments),
            system_events=tuple(self.system_events),
            local_commands=tuple(self._finalize_local_commands(command_anchor_uuids)),
            prompt_texts=anchor_texts,
            prompt_timestamps=anchor_ts,
            prompt_image_parts=anchor_images,
            prompt_ids=anchor_prompt_ids,
            prompt_expansions=command_expansions,
            tool_use_to_turn_uuid=self._tool_use_to_turn_uuid(),
            rewinds=self._compute_rewinds(),
        )

    def _anchor_side_tables(
        self, anchor_texts: dict[str, str],
    ) -> tuple[dict[str, str], dict[str, list]]:
        """Timestamp + inline-image side tables for the anchored prompts,
        filtered to the same uuid set as `anchor_texts`."""
        anchor_ts = {
            u: self.prompt_timestamps[u]
            for u in anchor_texts
            if u in self.prompt_timestamps
        }
        anchor_images = {
            u: self.prompt_image_parts[u]
            for u in anchor_texts
            if u in self.prompt_image_parts
        }
        return anchor_ts, anchor_images

    def _anchor_prompt_texts(
        self, finalized: list[TurnUsage], promoted_texts: dict[str, str],
    ) -> dict[str, str]:
        """uuid → text for the prompt entry that triggered each turn.

        `promoted_texts` carries the display text for anchored skill commands
        (e.g. `/review`) that have no typed-prompt entry in `prompt_texts` —
        merged in here rather than written back onto the accumulator.

        Background-task completions (`<task-notification>` user entries)
        are excluded: they get a dedicated `task.notification` span and the
        projection deliberately nests their turn under the previous prompt
        rather than starting a new one, so they must not become a `prompt`
        turn anchor."""
        out: dict[str, str] = {}
        for t in finalized:
            pu = t.prompt_uuid
            if not pu:
                continue
            text = self.prompt_texts.get(pu) or promoted_texts.get(pu)
            if text and not text.lstrip().startswith('<task-notification>'):
                out[pu] = text
        return out

    def _finalize_local_commands(
        self, command_anchor_uuids: set[str],
    ) -> list[TranscriptLocalCommand]:
        out: list[TranscriptLocalCommand] = []
        for ce in self.lc_commands:
            # A prompt-expanding skill command (it survived the isMeta-child
            # gate into `command_anchor_uuids`) is emitted as the turn's
            # `prompt-<uuid>` anchor, never a local-command card — even before
            # its response lands. Suppressing it here from the very first
            # firing means its uuid is never marked seen as a local command,
            # so the live anchor poster isn't blocked once the turn appears.
            if ce['uuid'] in command_anchor_uuids:
                continue
            stdout = self.lc_stdout_by_parent.get(ce['uuid'])
            caveat_uuid = (
                ce['parent_uuid']
                if ce['parent_uuid'] in self.lc_caveat_uuids
                else None
            )
            out.append(TranscriptLocalCommand(
                command_uuid=ce['uuid'],
                command_name=ce['name'],
                args=ce['args'],
                timestamp=ce['timestamp'],
                stdout_uuid=stdout['uuid'] if stdout else None,
                stdout_text=stdout['text'] if stdout else None,
                caveat_uuid=caveat_uuid,
                stderr_text=stdout.get('stderr') if stdout else None,
            ))
        return out
