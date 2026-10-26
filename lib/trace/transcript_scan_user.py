"""User-entry handling for the transcript scan: prompts, tool_result patching and
local (slash / bang) command collection."""

from __future__ import annotations

from lib.tokens.token_estimator import (
    estimate_content_tokens,
    estimate_image_only_tokens,
)
from lib.trace.prompt_images_resolve import extract_image_parts
from lib.trace.transcript_parsers import _normalize_dict_keys
from lib.trace.transcript_scan_helpers import (
    _BASH_INPUT_RE,
    _BASH_STDERR_RE,
    _BASH_STDOUT_RE,
    _COMMAND_ARGS_RE,
    _COMMAND_NAME_RE,
    _LOCAL_COMMAND_CAVEAT_TAG,
    _LOCAL_COMMAND_STDOUT_RE,
    _capture_tool_input,
    _extract_tool_result_text,
    _first_block_is_tool_result,
    _is_real_prompt,
    _local_command_text,
    _maybe_str,
    _user_prompt_text,
)


class _UserEntryScanMixin:
    """User / tool_result / local-command handlers of `_TranscriptScan`."""

    # ---- user / tool_result --------------------------------------------------

    def _handle_user_entry(self, entry_n: dict, eparent: str | None) -> None:
        """Route a user entry to prompt/tool_result handling, and record the
        isMeta-expansion parentage that gates command anchoring (a skill like
        /review emits its expansion as a direct isMeta child of the command
        echo — display commands / workflow-resume nudges do not). Also capture
        the full expansion text so it reaches the conversation view."""
        self._note_tool_interrupt(entry_n)
        self._handle_user_message(entry_n)
        self._patch_skill_payload(entry_n)
        if entry_n.get('is_meta') and eparent:
            self.meta_expansion_parents.add(eparent)
            # Capture the isMeta child's text keyed by parent so expansions
            # don't cross-wire when multiple slash commands are in one session.
            msg = _normalize_dict_keys(entry_n.get('message'))
            content = msg.get('content')
            text = _user_prompt_text(content)
            if text:
                self.meta_expansion_texts[eparent] = text

    def _note_tool_interrupt(self, entry_n: dict) -> None:
        """Record the assistant message id when the user interrupted a tool
        call. Claude Code writes this as a user entry carrying
        `interruptedMessageId` (the interrupted assistant `message.id`, which
        is exactly a builder's dedup key). `finalize` uses it to flag that
        turn's unresolved tool_calls as interrupted."""
        imid = _maybe_str(entry_n.get('interrupted_message_id'))
        if imid:
            self.interrupted_message_ids.add(imid)

    def _handle_user_message(self, entry_n: dict) -> None:
        msg = _normalize_dict_keys(entry_n.get('message'))
        content = msg.get('content')
        if not _first_block_is_tool_result(content):
            self._record_prompt_entry(entry_n, content)
            return
        if not isinstance(content, list):
            return
        # A single user entry can carry multiple tool_result blocks;
        # patch each onto its originating turn's tool_calls list.
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'tool_result':
                self._patch_tool_result_block(block)

    def _record_prompt_entry(self, entry_n: dict, content: object) -> None:
        """Register a real-prompt user entry and capture its anchor side
        tables (text / timestamp / inline image parts), keyed by uuid.

        Called for non-tool_result user entries. Only entries that pass
        `_is_real_prompt` join `real_prompt_uuids`; synthetic shapes
        (image-source carriers, `<task-notification>` completions,
        local-command echoes, meta/sidechain) are skipped so the
        parentUuid walk passes straight through them to the prompt that
        actually opened the turn. The image parts are the durable
        fallback for `prompt_images` when the live image cache is gone."""
        euuid = _maybe_str(entry_n.get('uuid'))
        if not euuid:
            return
        text = _user_prompt_text(content)
        if text and text.lstrip().startswith('<task-notification>'):
            self.task_notification_uuids.add(euuid)
        if not _is_real_prompt(entry_n, content, text):
            return
        self.real_prompt_uuids.add(euuid)
        if text:
            self.prompt_texts[euuid] = text
        self._capture_prompt_side_tables(euuid, entry_n, content)

    def _capture_prompt_side_tables(
        self, euuid: str, entry_n: dict, content: object,
    ) -> None:
        """Capture the anchor side tables (timestamp / prompt_id / inline
        image parts) for a real-prompt entry, keyed by uuid."""
        ts = entry_n.get('timestamp')
        if isinstance(ts, str) and ts:
            self.prompt_timestamps[euuid] = ts
        pid = entry_n.get('prompt_id')
        if isinstance(pid, str) and pid:
            self.prompt_ids[euuid] = pid
        parts = extract_image_parts(content)
        if parts:
            self.prompt_image_parts[euuid] = parts

    def _patch_tool_result_block(self, block: dict) -> None:
        tu_id = block.get('tool_use_id')
        if not isinstance(tu_id, str):
            return
        owner = self.tool_use_to_turn.get(tu_id)
        if owner is None:
            return
        is_error = bool(block.get('is_error', False))
        inner = block.get('content')
        input_est = estimate_content_tokens(inner)
        image_est = estimate_image_only_tokens(inner)
        result_text = _extract_tool_result_text(inner) if is_error else None
        for call in owner.tool_calls:
            if call.get('id') != tu_id:
                continue
            call['is_error'] = is_error
            # A Skill body may already have been attributed here; the
            # tool_result measures only the one-line launch acknowledgement,
            # so re-add it rather than clobbering.
            call['input_token_estimate'] = (
                input_est + (call.get('skill_payload_token_estimate') or 0)
            )
            call['image_token_estimate'] = image_est
            if result_text is not None:
                call['result_text'] = result_text
            if is_error:
                self._capture_deny_input(call, tu_id)
            return

    def _patch_skill_payload(self, entry_n: dict) -> None:
        """Attribute a `Skill` launch's injected body to its own tool call.

        Claude Code does not deliver a skill's content as the Skill
        tool_result — that is a one-liner ("Launching skill: foo"). The body
        arrives as a *separate* isMeta user entry carrying `sourceToolUseID`,
        which no other branch of the scan claims, so without this the cost is
        unattributed: a skill injecting 150k tokens reports the 8 tokens of
        its one-line result. Additive because the tool_result patch can land
        either side of the body in the linear scan.
        """
        if not entry_n.get('is_meta'):
            return
        tu_id = _maybe_str(entry_n.get('source_tool_use_id'))
        if not tu_id:
            return
        owner = self.tool_use_to_turn.get(tu_id)
        if owner is None:
            return
        msg = _normalize_dict_keys(entry_n.get('message'))
        est = estimate_content_tokens(msg.get('content'))
        if not est:
            return
        for call in owner.tool_calls:
            if call.get('id') == tu_id:
                prior = call.get('skill_payload_token_estimate') or 0
                call['skill_payload_token_estimate'] = prior + est
                call['input_token_estimate'] = (
                    (call.get('input_token_estimate') or 0) + est
                )
                return

    def _capture_deny_input(self, call: dict, tu_id: str) -> None:
        raw_input = self.tool_use_inputs.get(tu_id)
        if raw_input is None:
            return
        captured, dropped = _capture_tool_input(raw_input)
        if captured is None:
            return
        call['tool_input'] = captured
        if dropped:
            call['tool_input_truncated_bytes'] = dropped

    # ---- local command -------------------------------------------------------

    def _collect_local_command(
        self,
        entry_n: dict,
        etype: str | None,
        euuid: str | None,
        eparent: str | None,
    ) -> None:
        lc_text = _local_command_text(entry_n, etype)
        if not lc_text or not euuid:
            return
        if '<command-name>' in lc_text:
            self._collect_command_name(entry_n, lc_text, euuid, eparent)
        elif '<bash-input>' in lc_text:
            self._collect_bash_input(entry_n, lc_text, euuid)
        elif '<bash-stdout>' in lc_text and eparent:
            self._collect_bash_stdout(lc_text, euuid, eparent)
        elif '<local-command-stdout>' in lc_text and eparent:
            self._collect_local_stdout(lc_text, euuid, eparent)
        elif _LOCAL_COMMAND_CAVEAT_TAG in lc_text:
            self.lc_caveat_uuids.add(euuid)

    def _collect_bash_stdout(self, lc_text: str, euuid: str, eparent: str) -> None:
        out_m = _BASH_STDOUT_RE.search(lc_text)
        err_m = _BASH_STDERR_RE.search(lc_text)
        err = err_m.group(1) if err_m else None
        self.lc_stdout_by_parent[eparent] = {
            'uuid': euuid,
            'text': out_m.group(1) if out_m else '',
            'stderr': err or None,
        }

    def _collect_local_stdout(self, lc_text: str, euuid: str, eparent: str) -> None:
        m_out = _LOCAL_COMMAND_STDOUT_RE.search(lc_text)
        self.lc_stdout_by_parent[eparent] = {
            'uuid': euuid,
            'text': m_out.group(1) if m_out else '',
        }

    def _collect_command_name(
        self,
        entry_n: dict,
        lc_text: str,
        euuid: str,
        eparent: str | None,
    ) -> None:
        m_name = _COMMAND_NAME_RE.search(lc_text)
        if not m_name:
            return
        m_args = _COMMAND_ARGS_RE.search(lc_text)
        ts = entry_n.get('timestamp')
        name = m_name.group(1).strip()
        args = (m_args.group(1).strip() if m_args else None) or None
        self.lc_commands.append({
            'uuid': euuid,
            'name': name,
            'args': args,
            'timestamp': ts if isinstance(ts, str) else None,
            'parent_uuid': eparent,
        })
        # Register this slash command as a candidate turn anchor (see
        # `command_prompt_uuids`). The display text mirrors what the user
        # typed; the timestamp lets the anchor sit at command time on the
        # timeline (the side tables key on the same uuid as `prompt_texts`).
        self.command_prompt_uuids.add(euuid)
        self.command_prompt_texts[euuid] = f'{name} {args}' if args else name
        if isinstance(ts, str) and ts:
            self.prompt_timestamps.setdefault(euuid, ts)
        pid = entry_n.get('prompt_id')
        if isinstance(pid, str) and pid:
            self.prompt_ids.setdefault(euuid, pid)

    def _collect_bash_input(self, entry_n: dict, lc_text: str, euuid: str) -> None:
        # `!ls` → command_name `!ls`. The leading `!` distinguishes a bash
        # command from a slash command in the UI. No caveat in bash mode,
        # so parent_uuid is irrelevant (never matches an lc_caveat_uuid).
        m = _BASH_INPUT_RE.search(lc_text)
        if not m:
            return
        ts = entry_n.get('timestamp')
        self.lc_commands.append({
            'uuid': euuid,
            'name': f'!{m.group(1).strip()}',
            'args': None,
            'timestamp': ts if isinstance(ts, str) else None,
            'parent_uuid': None,
        })
