"""Unwrapping of Claude Code's `<pasted_content>` blocks.

Claude Code turns any bracketed paste whose trimmed body is >= 20 characters
into an inline block and sends THAT to the model, so the transcript turn is no
longer byte-equal to what was pasted:

    <pasted_content id="6eed">
    the pasted body
    </pasted_content id="6eed">

The closing tag repeating the id is Claude Code's own spelling, not a typo.
The id is `sha256(<session key>)[:4]`, so every block in one session shares it
and it is stable across runs — never treat it as a per-paste nonce.

regin's bridge delivers every steer through bracketed paste (the only input
mode that survives a body the terminal cannot take in one burst), so the
wrapper lands on prompts regin itself sent and any comparison against the
delivered body has to see through it.
"""

from __future__ import annotations

import re

_BLOCK_RE = re.compile(
    r'<pasted_content id="([0-9a-f]{4})">\n(.*?)\n</pasted_content id="\1">',
    re.DOTALL)


def unwrap_pasted_content(text) -> str:
    """`text` with every well-formed block replaced by its body. Idempotent,
    and a no-op on text that carries no block or a malformed one."""
    return _BLOCK_RE.sub(lambda m: m.group(2), str(text or ''))
