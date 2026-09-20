"""The JSON a composer posts to attach one image, decoded.

Base64 in JSON rather than multipart because that is what the
`bridge-attachments` ingest takes and what the SPA's api client can send
without a second transport. Shared because three blueprints — tasks, features,
defects — take the identical body, and the third copy is where the first one
would have started drifting.
"""

from __future__ import annotations

import base64
import binascii
from typing import Optional


def decoded_image(payload) -> bytes:
    """The bytes a composer sent, or ValueError with the reason."""
    raw = (payload or {}).get("data_b64")
    if not isinstance(raw, str) or not raw:
        raise ValueError("data_b64 required")
    try:
        return base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("data_b64 is not valid base64") from None


def wanted_idx(payload) -> Optional[int]:
    """The `idx` the caller asks for, or None.

    A *proposal*, not a demand — the allocator falls back to the next free
    index. It exists for the composer case: bytes pasted before the row existed
    already have an `![](image:N)` reference sitting in the body at the caret,
    so the upload that finally lands them has to be able to ask for the N the
    text already names. Anything that is not a positive int is None, because a
    malformed proposal should attach the image, not refuse it.
    """
    raw = (payload or {}).get("idx")
    return raw if isinstance(raw, int) and not isinstance(raw, bool) \
        and raw >= 1 else None
