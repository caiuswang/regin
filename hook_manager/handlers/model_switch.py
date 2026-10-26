"""PostModelSwitch → one `model.switch` span per change of the session's model.

Records the switch only; pricing is already per turn (`turn_usage.model`), so
nothing downstream re-segments cost on this span.
"""

from __future__ import annotations

from ..core import HookPayload, HookResponse
from .misc_events import _safe_emit

# The payload names why the model changed in `source` (command / picker / sdk
# / auto / resume); the span calls it `reason` so `source` keeps meaning the
# hook writer across span kinds.
_FIELD_MAP = (
    ('from_model', 'from'),
    ('to_model', 'to'),
    ('source', 'reason'),
    ('requested_model', 'requested_model'),
    ('context_tokens', 'context_tokens'),
    ('prompt_cache_warm', 'prompt_cache_warm'),
    ('cache_ttl', 'cache_ttl'),
    ('estimated_cache_write_usd', 'estimated_cache_write_usd'),
    ('pricing', 'pricing'),
)


def handle(payload: HookPayload) -> HookResponse | None:
    raw = payload.raw or {}
    attrs = {dst: raw[src] for src, dst in _FIELD_MAP if raw.get(src) is not None}
    _safe_emit(payload, 'model.switch', attrs)
    return HookResponse(suppress_output=True)
