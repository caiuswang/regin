"""The topic-proposal *regenerate* directive — the "Prior draft reference" block
spliced into the drafting agent's prompt (as the ``prior_reference`` variable of
``topic-proposal-drafting``) when a run re-drafts an existing proposal.

Migrated verbatim from the f-string that was
``proposal_external._prior_reference_block`` so the regenerate-specific guidance
(reference-not-baseline, no changelog prose, revise-in-place) is editable through
prompt management instead of hardcoded. A characterization test
(tests/prompts/test_regenerate_parity.py) asserts render_surface == the frozen
reference builder; only ``{{ … }}`` is a placeholder."""

from __future__ import annotations

from lib.prompts.registry import (PromptVariable, register_retired_default,
                                  register_surface)

SURFACE_ID = "topic-proposal-regenerate"

_DEFAULT_BODY = """

Prior draft reference:
{{review_feedback}}{{scope_directive}}Use the previous proposal and wiki only as reference — to keep good coverage and to address any review feedback above — not as a baseline to diff against. Re-check every topic against the current repository.

Write each topic's wiki and the notes as a standalone description of the repository as it is NOW. Do NOT write changelog or diff prose comparing this revision to the previous one: avoid phrasing like "was removed", "is now", "no longer", "the old …", "previously", "changed from", or "renamed to". The reader has never seen the prior draft — describe the current structure and behavior directly, citing files that exist today.

A regenerate REVISES the page in place; it does not accrete. Keep each wiki's scope and length close to the prior draft — correct what the current code no longer matches and cut detail that has gone stale, rather than appending new file-by-file descriptions because some files changed. A drift note asking you to refresh a topic is a request to re-verify and tighten its existing narrative, not to grow it.

Previous proposal JSON (structural fields only — each topic's `wiki` body is omitted):
```json
{{prior_proposal_json}}
```

Previous wiki markdown (not embedded here — Read it yourself with your Read tool at this path): {{prior_wiki_pointer}}
"""


register_surface(
    SURFACE_ID,
    label="Topic proposal — regenerate directive",
    area="topic-proposal",
    default_body=_DEFAULT_BODY,
    description=(
        "The 'Prior draft reference' block spliced into the drafting agent's "
        "prompt when a run re-drafts an existing proposal: how to treat the prior "
        "draft (reference, not baseline), the no-changelog-prose rule, and the "
        "revise-in-place instruction. Empty on a fresh (non-regenerate) run."
    ),
    applies_to=("external-agent",),
    variables=(
        PromptVariable("review_feedback", "Rendered open review-feedback threads carried forward into this regenerate, or empty when there is none.", required=False),
        PromptVariable("scope_directive", "The scoped-refresh directive naming the drifted topics to re-derive, or empty on a full re-draft.", required=False),
        PromptVariable("prior_proposal_json", "The previous draft's proposal JSON, structural fields only (agent self-notes and wiki bodies stripped)."),
        PromptVariable("prior_wiki_pointer", "Absolute path of the previous draft's on-disk wiki markdown, or `(no wiki on file)` when there is none to Read."),
    ),
)

# Retired default-body hashes: an un-edited stale row still hashing to one of
# these is healed to the current default by `seed_builtin_skeletons`, so a
# body change reaches existing installs instead of being pinned to the stale
# seed. Append a line each time the body changes.
for _sha in (
    # embedded full prior-wiki fence + proposal JSON with wiki bodies, before
    # the evidence-pointer form (wiki path, structural JSON only)
    "9efb8a1ead718ef73d3a823b031b9019d7bd1f53e019675b049369aa8712f09c",
):
    register_retired_default(SURFACE_ID, sha256=_sha)

__all__ = ["SURFACE_ID"]
