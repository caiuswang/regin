"""Resolve the deep judge's system prompt — default, override, and aspects.

The deep correctness/process judges keep their built-in system prompts as
module constants (`agentic._PROMPT`, `process_agentic._PROMPT`). This module
layers two reviewer-configurable inputs on top, without the judge modules
needing to know about settings:

1. the **base body**, resolved dual-read (`_resolve_base`): an edited DB
   skeleton row (`prompt_templates` slug `grader-correctness`/`grader-process`,
   registered in `lib/prompts/surfaces/grader.py`) wins; else the legacy
   per-axis **override** (`settings.grader.system_prompt_overrides[axis]`,
   blank/missing → skip); else the built-in default. A DB row still equal to
   the seeded default is treated as unedited, so existing settings overrides
   keep working until a reviewer actually edits the skeleton; and
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

# axis → the editable surface skeleton whose stored body becomes the new
# (DB-backed) source of the judge's base prompt when a reviewer edits it.
_AXIS_SURFACE = {
    "correctness": "grader-correctness",
    "process": "grader-process",
}


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


def _edited_skeleton_body(axis: str, default: str) -> str | None:
    """The DB skeleton body for `axis` **iff a reviewer edited it away from the
    built-in default**, else None.

    A freshly-seeded row carries the default verbatim (`seed_builtin_skeletons`
    copies `surface.default_body()`), so an equal body is treated as "not
    edited" — that's what lets the legacy `settings` override still take effect
    when the skeleton is untouched. Imported lazily so importing this module
    doesn't require the ORM, and returns None (never raises) for an axis with
    no mapped surface. A blank stored body is ignored, mirroring how a blank
    settings override falls through to the default."""
    surface_id = _AXIS_SURFACE.get(axis)
    if not surface_id:
        return None
    from sqlalchemy.exc import OperationalError

    from lib.prompt_templates import get_template_by_slug

    try:
        row = get_template_by_slug(surface_id)
    except OperationalError:
        # An abnormally initialized DB (no prompt_templates table) must not
        # break grading — fall through to the settings override / built-in.
        return None
    if not row:
        return None
    body = row.get("body")
    if not isinstance(body, str) or not body.strip():
        return None
    return body if body != default else None


def _resolve_base(axis: str, default: str) -> str:
    """The judge prompt base for `axis`, *before* token fills and the aspects
    splice. Dual-read precedence: an edited DB skeleton body wins; else the
    legacy per-axis `settings.grader.system_prompt_overrides` (blank → skip);
    else the built-in `default`."""
    edited = _edited_skeleton_body(axis, default)
    if edited is not None:
        return edited
    overrides = settings.grader.system_prompt_overrides or {}
    override = overrides.get(axis)
    if isinstance(override, str) and override.strip():
        return override.strip()
    return default


def judge_system_prompt(
    axis: str, default: str, *, substitutions: dict[str, str] | None = None,
    enabled_aspects: list[str] | None = None,
) -> str:
    """The deep judge system prompt for `axis`: the resolved base (edited DB
    skeleton → legacy settings override → built-in `default`), with the
    enabled-aspects block inserted just before `<output_format>` so it can't
    dilute the strict-JSON close.

    `substitutions` ({token: value}, e.g. the `{trace_id}`/`{python}` fills) is
    applied to the base *before* the reviewer-configured aspects block is
    spliced in. The block carries free-form reviewer text, so substituting
    after splicing would let an aspect description that happens to contain a
    literal `{python}`/`{trace_id}` token be clobbered with the trace dump.
    Substituting only the base keeps the aspect text verbatim."""
    base = _resolve_base(axis, default)
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
