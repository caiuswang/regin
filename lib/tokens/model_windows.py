"""Model -> context window size lookup.

Values come from Anthropic's official model docs
(https://platform.claude.com/docs/en/docs/about-claude/models/overview):

- Claude Opus 4.7   -> 1M tokens (native)
- Claude Sonnet 4.6 -> 1M tokens (native)
- Claude Opus 4.6   -> 1M tokens (native)
- Claude Haiku 4.5  -> 200K tokens
- Older 4.x Sonnet/Opus (4.5, 4.1, 4)   -> 200K tokens

The historical `[1m]` suffix predates Opus 4.7 / Sonnet 4.6 having a 1M
native window; it's kept here as an alias so transcripts written by older
Claude Code builds still resolve. Users can override or extend the table
via `settings.model_context_windows` in `settings.json`:

    {"model_context_windows": {"my-custom-model": 500000}}
"""

from __future__ import annotations


# Built-in defaults. Override via `settings.model_context_windows`.
_BUILTIN_WINDOWS: dict[str, int] = {
    # Current frontier models
    "claude-opus-4-7": 1_000_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude-haiku-4-5": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    # Legacy
    "claude-opus-4-6": 1_000_000,
    "claude-sonnet-4-5": 200_000,
    "claude-sonnet-4-5-20250929": 200_000,
    "claude-opus-4-5": 200_000,
    "claude-opus-4-5-20251101": 200_000,
    "claude-opus-4-1": 200_000,
    "claude-opus-4-1-20250805": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-sonnet-4-20250514": 200_000,
    "claude-opus-4": 200_000,
    "claude-opus-4-20250514": 200_000,
    # `[1m]` suffix kept for back-compat with older Claude Code transcripts
    # that tagged the extended-context beta variant explicitly.
    "claude-opus-4-7[1m]": 1_000_000,
    "claude-sonnet-4-6[1m]": 1_000_000,
    "claude-opus-4-6[1m]": 1_000_000,
}
DEFAULT_WINDOW = 200_000


def _table() -> dict[str, int]:
    """Built-in table merged with user overrides from settings."""
    try:
        from lib.settings import settings
        overrides = getattr(settings, "model_context_windows", None) or {}
    except Exception:
        overrides = {}
    if not overrides:
        return _BUILTIN_WINDOWS
    merged = dict(_BUILTIN_WINDOWS)
    for k, v in overrides.items():
        try:
            merged[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return merged


def window_for(model: str | None) -> int:
    if not model:
        return DEFAULT_WINDOW
    table = _table()
    if model in table:
        return table[model]
    # Unknown dated alias (e.g. `claude-opus-4-7-20260101`) — strip the
    # trailing date and retry against the base id.
    base = model.rsplit('-', 1)[0] if model.count('-') >= 3 else model
    return table.get(base, DEFAULT_WINDOW)


def infer_window(model: str | None, peak_tokens: int) -> int:
    """Resolve the context window for a model.

    `peak_tokens` is the highest observed prompt-token total for the
    session and is retained for back-compat: when the configured base is
    200K but the transcript flew past it, we still try a `[1m]` alias
    once before giving up. We never silently grow the window past a
    known size — if the model isn't in the table at all, we treat
    `peak_tokens` as the cap so the frontend doesn't divide by zero.
    """
    if not model:
        return DEFAULT_WINDOW
    base = window_for(model)
    if peak_tokens <= base:
        return base
    table = _table()
    extended = table.get(f"{model}[1m]")
    if extended and extended > base:
        return extended
    # Model is known but observed peak exceeds it. Trust the configured
    # window over the observation — the % can show >100% rather than
    # silently inflating the denominator.
    if model in table or model.rsplit('-', 1)[0] in table:
        return base
    # Truly unknown model: fall back to peak so we never divide by zero.
    return max(base, peak_tokens)
