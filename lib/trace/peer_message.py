"""Recognising a harness-delivered peer message (`<agent-message from=…>`).

Claude Code delivers a subagent hand-back, or a message from another session,
as a prompt: through UserPromptSubmit when the session is idle, or as a
`queued_command` attachment when it is busy. Neither is something the user
typed.

The attachment says so structurally (`origin.kind == 'peer'`). The
UserPromptSubmit payload carries no origin, so the hook can only recognise the
frame. That is reliable because the harness opens the frame at column zero and
indents every line of the body inside it, so no quoted report can start a
prompt with it.
"""

from __future__ import annotations

PEER_FRAME = '<agent-message from='

HUMAN_ORIGIN = 'human'


def is_peer_message(text) -> bool:
    """True when `text` is a harness peer-message frame, not a typed prompt."""
    return isinstance(text, str) and text.startswith(PEER_FRAME)


def is_non_human_origin(origin) -> bool:
    """True when a transcript `origin` names a producer other than the user.

    Transcripts written before Claude Code stamped `origin` carry none, and
    those prompts were typed, so a missing origin counts as human."""
    if not isinstance(origin, dict):
        return False
    kind = origin.get('kind')
    return isinstance(kind, str) and kind != HUMAN_ORIGIN
