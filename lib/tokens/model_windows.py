"""Model -> context window size lookup.

Values come from Anthropic's official model docs
(https://platform.claude.com/docs/en/docs/about-claude/models/overview):

- Claude Fable 5    -> 1M tokens (native)
- Claude Opus 4.8 / 4.7 -> 1M tokens (native)
- Claude Sonnet 4.6 -> 1M tokens (native)
- Claude Opus 4.6   -> 1M tokens (native)
- Claude Haiku 4.5  -> 200K tokens
- Older 4.x Sonnet/Opus (4.5, 4.1, 4)   -> 200K tokens

Models missing from this table resolve through the models.dev catalogue
(`lib.tokens.pricing.model_context_limit`, the same source pricing uses),
so a just-launched model gets its real window instead of the 200K default.
The 200K fallback only applies when the model is in neither place.

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
    "claude-fable-5": 1_000_000,
    "claude-opus-4-8": 1_000_000,
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


def _catalogue_window(bare: str, base: str) -> int | None:
    """Context limit from the models.dev catalogue (pricing's cache).

    Keeps brand-new models (e.g. `claude-fable-5`) from silently landing
    on the 200K default until someone hand-edits the builtin table.
    Degrades to None offline — pricing never raises.
    """
    try:
        from lib.tokens.pricing import model_context_limit
    except ImportError:
        return None
    limit = model_context_limit(bare)
    if limit is None and base != bare:
        limit = model_context_limit(base)
    return limit


def _resolve_window(model: str) -> int | None:
    """Window for a known model, or None when nothing matched."""
    table = _table()
    if model in table:
        return table[model]
    # Strip a trailing variant suffix (e.g. the `[1m]` extended-context
    # tag) and retry, so an override keyed on the bare model id also
    # covers the suffixed transcript id
    # (`claude-opus-4-8[1m]` -> `claude-opus-4-8`). Without this, a
    # user-configured window only applied to the exact `[1m]` key, and
    # bare-id overrides silently fell through to the 200K default.
    bare = model.split('[', 1)[0]
    if bare != model and bare in table:
        return table[bare]
    # Unknown dated alias (e.g. `claude-opus-4-7-20260101`) — strip the
    # trailing date and retry against the base id. Operate on the
    # suffix-stripped id so dated `[1m]` variants resolve too.
    base = bare.rsplit('-', 1)[0] if bare.count('-') >= 3 else bare
    if base in table:
        return table[base]
    limit = _catalogue_window(bare, base)
    if limit:
        return limit
    # The `[1m]` tag explicitly names the 1M-context variant; honor it
    # even when the model id is otherwise unknown.
    if bare != model and model.endswith('[1m]'):
        return 1_000_000
    return None


def window_for(model: str | None) -> int:
    if not model:
        return DEFAULT_WINDOW
    return _resolve_window(model) or DEFAULT_WINDOW


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
    known = _resolve_window(model)
    base = known or DEFAULT_WINDOW
    if peak_tokens <= base:
        return base
    extended = _table().get(f"{model}[1m]")
    if extended and extended > base:
        return extended
    # Model is known (table, override, or catalogue) but observed peak
    # exceeds it. Trust the configured window over the observation — the
    # % can show >100% rather than silently inflating the denominator.
    if known:
        return base
    # Truly unknown model: fall back to peak so we never divide by zero.
    return max(base, peak_tokens)
