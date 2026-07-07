"""The shared *authoring standards* fragment — the single definition of what
makes a good topic, injected into BOTH the drafting prompt and the review prompt
via ``{{include:topic-authoring-standards}}``.

The point is that the drafter and the reviewer judge a topic against the *same*
bar and can never drift apart: previously the drafter was told "keep it tight,
don't catalog every file, stay in your lane" while the reviewer was only told
"assess coverage", so the reviewer rewarded breadth the drafter was told to
avoid. One fragment, two consumers, no drift."""

from __future__ import annotations

from lib.prompts.registry import register_surface

SURFACE_ID = "topic-authoring-standards"

_DEFAULT_BODY = """A good topic — whether you are drafting one or reviewing one — meets all of these; a draft with broad coverage that breaks them is NOT sound:
- Every topic is justified by real repository files; every ref path exists in the repo (regin rejects paths it can't find on disk).
- A ref's `role` is optional; when you set one, use only: overview, architecture, entrypoint, api, schema, test, migration, implementation, config, docs. Omit it if none clearly fits.
- A ref's `tier` is optional and orthogonal to `role`: it says how central the file is to THIS wiki. `"reference"` marks a file the wiki only points at as context or an example and does NOT explain in detail — those are excluded from content-drift, so editing them won't nag for a refresh. `"primary"` (or absent) marks a file the wiki actually describes. Tag reference-only files generously — it is the main lever for keeping drift low — and a file should be `primary` in only the ONE topic whose wiki explains it; if two topics both describe the same file, one of them is out of scope.
- A topic `wiki` is a durable conceptual overview, not a file-by-file catalog: explain the topic's purpose, mental model, main flows, and the invariants/gotchas that outlast the code, and cite specific files or functions only where they anchor that narrative. Keep it tight — a well-scoped page stays roughly the same length as the topic evolves. Do not try to describe every ref; pointer-only (`tier: "reference"`) files need no prose.
- Every topic is a self-contained page for ONE subject. Do NOT duplicate or restate a sibling topic's territory — when your topic is adjacent, scope it to what the sibling does not cover and cross-link the sibling with `[[id]]` instead of re-explaining it. Shared framing that spans topics goes in the top-level `overview`, not repeated into each page.
- `parent_id` places the topic under one top-level navigation bucket; pick the single best-fitting id, or `null` if none fits (do NOT force a weak fit). `blurb` is a one-line router card ("what task should drill in here"), not a description; omit it and `intent` is used instead."""


register_surface(
    SURFACE_ID,
    label="Topic authoring standards (shared fragment)",
    area="topic-proposal",
    default_body=_DEFAULT_BODY,
    description=(
        "The shared quality bar for a topic — what makes a wiki tight, correctly "
        "tiered, and in-scope. Injected into the drafting and review prompts via "
        "{{include:topic-authoring-standards}} so both judge against one standard."
    ),
    kind="fragment",
)

__all__ = ["SURFACE_ID"]
