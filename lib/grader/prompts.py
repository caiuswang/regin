"""Resolve the deep judge's system prompt — default, override, and aspects.

The deep correctness/process judges keep their built-in system prompts as
module constants (`agentic._PROMPT`, `process_agentic._PROMPT`). This module
layers two reviewer-configurable inputs on top, without the judge modules
needing to know about settings:

1. a per-axis **override** (`settings.grader.system_prompt_overrides[axis]`) —
   a blank/missing value falls back to the built-in default; and
2. the enabled **aspects** (`settings.grader.aspects`), rendered into an
   `<aspects>` block the judge is told to also weigh. Aspects never add a
   grounded axis — they only shape what the judge attends to.

Both judge modules call `judge_system_prompt(axis, default, substitutions=...)`,
which fills the `{trace_id}`/`{python}` placeholders in the base prompt before
the (free-form, reviewer-authored) aspects block is spliced in — so a literal
placeholder token inside an aspect description is never clobbered.
"""

from __future__ import annotations

from lib.settings import settings

_OUTPUT_MARKER = "<output_format>"


def _enabled_aspects(enabled_keys: list[str] | None):
    """The aspect definitions to render: a per-run key whitelist when
    `enabled_keys` is given, else each aspect's persisted `.enabled` flag."""
    aspects = settings.grader.aspects or []
    if enabled_keys is None:
        return [a for a in aspects if getattr(a, "enabled", True)]
    keyset = set(enabled_keys)
    return [a for a in aspects if getattr(a, "key", "") in keyset]


def render_aspects_block(enabled_keys: list[str] | None = None) -> str:
    """The `<aspects>` block for the enabled aspects, or "" when none are on.

    `enabled_keys` is a per-run override: when given, exactly those aspect
    keys are rendered (label/description still pulled from the configured
    `settings.grader.aspects` definitions). When None, fall back to each
    aspect's persisted `.enabled` flag."""
    enabled = _enabled_aspects(enabled_keys)
    if not enabled:
        return ""
    lines = [
        "<aspects>",
        "Beyond the axis above, also weigh these reviewer-configured aspects "
        "and call out in your reasons any that materially fail:",
    ]
    for aspect in enabled:
        label = getattr(aspect, "label", None) or getattr(aspect, "key", "")
        desc = getattr(aspect, "description", "") or ""
        lines.append(f"- {label}: {desc}".rstrip())
    lines.append("</aspects>")
    return "\n".join(lines)


def judge_system_prompt(
    axis: str, default: str, *, substitutions: dict[str, str] | None = None,
    enabled_aspects: list[str] | None = None,
) -> str:
    """The deep judge system prompt for `axis`: the per-axis override (or the
    built-in `default` when blank), with the enabled-aspects block inserted
    just before `<output_format>` so it can't dilute the strict-JSON close.

    `substitutions` ({token: value}, e.g. the `{trace_id}`/`{python}` fills) is
    applied to the override/default base *before* the reviewer-configured
    aspects block is spliced in. The block carries free-form reviewer text, so
    substituting after splicing would let an aspect description that happens to
    contain a literal `{python}`/`{trace_id}` token be clobbered with the trace
    dump. Substituting only the base keeps the aspect text verbatim."""
    overrides = settings.grader.system_prompt_overrides or {}
    override = overrides.get(axis)
    base = override.strip() if isinstance(override, str) and override.strip() else default
    for token, value in (substitutions or {}).items():
        base = base.replace(token, value)
    block = render_aspects_block(enabled_aspects)
    if not block:
        return base
    if _OUTPUT_MARKER in base:
        return base.replace(_OUTPUT_MARKER, f"{block}\n\n{_OUTPUT_MARKER}", 1)
    return f"{base}\n\n{block}"


def default_system_prompts() -> dict[str, str]:
    """The built-in (un-overridden) judge prompts, for the config UI's
    'reset to default' affordance. Imported lazily to avoid an import cycle."""
    from lib.grader.agentic import _PROMPT as correctness_default
    from lib.grader.process_agentic import _PROMPT as process_default
    return {"correctness": correctness_default, "process": process_default}


__all__ = ["judge_system_prompt", "render_aspects_block",
           "default_system_prompts"]
