"""The topic-proposal *drafting* agent prompt — regin's flagship "goal for an
external agent". Migrated verbatim from the f-string that was
``proposal_external._instructions`` (the JSON-shape braces are literal single
braces here; only ``{{ … }}`` is a placeholder)."""

from __future__ import annotations

from lib.prompts.registry import PromptVariable, register_surface

SURFACE_ID = "topic-proposal-drafting"

# NOTE: keep byte-identical to the old f-string output — a characterization test
# (tests/prompts/test_drafting_parity.py) asserts render_surface == the frozen
# reference builder. Edit the wording here and the reference together.
_DEFAULT_BODY = """# Regin Topic Proposal Agent Task

Inspect this repository as needed and draft reviewable topic graph proposals.

User topic request:
{{topic_request}}{{prior_reference}}{{custom_instructions}}

Rules:
- Do not modify `.regin/topics/topic.json` or approved topic data.
- Write final JSON to the temp output file `{{temp_output_path}}`.
- Do not write `{{output_file}}` directly; regin will validate and copy the temp output into that canonical artifact.
- You may also print the same JSON as a fenced `json` block.
- Keep all file paths relative to the repository root.
- Only propose topics justified by real repository files; every ref path must exist in the repo (regin rejects paths it can't find on disk).
- A ref's `role` is optional; when you set one, use only: overview, architecture, entrypoint, api, schema, test, migration, implementation, config, docs. Omit it if none clearly fits.
- `aliases` are *alternate* phrases a future agent might search for — not restatements of the `id` or `label`. Do NOT list the topic id or label, and do NOT add variants that differ only in case, spacing, or hyphenation: regin normalizes aliases (lowercased, every run of non-alphanumeric characters → a single space), so `foo-bar`, `Foo Bar`, and `foo bar` all collapse to the same key and a repeat is rejected at apply time. Give 0–6 genuinely distinct phrasings, or leave the list empty.
- `parent_id` places the topic under one top-level navigation bucket (see "Available buckets" below). Pick the single best-fitting bucket id. If none clearly fits, set it to `null` — the reviewer will place it; do NOT force a weak fit. `blurb` is a one-line router card ("what task should drill in here"), not a description; omit it and `intent` is used instead.
- Every topic MUST include its own `wiki`: a self-contained Markdown page describing THAT topic — its files, behavior, and how it fits — because each topic becomes a separate `.regin/topics/wiki/<id>.md` page. Do NOT write one combined document covering every topic; give each topic its own distinct page. Put any shared framing that spans topics in the top-level `overview` instead, not repeated into each topic's wiki.
- If a write/tool permission prompt blocks writing the output file, stop and report the permission failure instead of printing a fallback success payload.

Signal completion (REQUIRED — do this LAST):
- After you have written the JSON to the temp output file, run this exact command to ingest your proposal and mark this run complete. It is the ONLY thing that finalizes the run — if you skip it, the run is treated as failed:

  {{finish_cmd}}

- The same command is available in the `REGIN_TOPIC_PROPOSAL_FINISH_CMD` environment variable. Run it once, as your final action, after the output file exists. Do not run it before the file is written.

Output JSON shape:
{
  "topics": [
    {
      "id": "short-stable-id",
      "label": "Human label",
      "aliases": [],
      "intent": "What this topic helps future agents understand",
      "status": "active",
      "parent_id": "one-of-the-bucket-ids-below-or-null",
      "blurb": "One line: what task should drill into this topic",
      "refs": [{"path": "relative/path.py", "role": "implementation"}],
      "edges": [],
      "commands": [],
      "include_globs": ["path/**"],
      "exclude_globs": [],
      "evidence_paths": ["relative/path.py"],
      "wiki": "Markdown wiki page for THIS topic only — its own standalone narrative"
    }
  ],
  "notes": [],
  "overview": "Optional short markdown intro tying the proposed topics together"
}

Existing approved topics (do not propose duplicates; explore the repo with your Read/Glob/Grep tools for everything else):
```json
{{existing_topics_json}}
```

Available buckets (pick one id for each topic's `parent_id`, or null if none fits):
```json
{{buckets_json}}
```
{{sibling_section}}"""


register_surface(
    SURFACE_ID,
    label="Topic proposal — drafting agent",
    area="topic-proposal",
    default_body=_DEFAULT_BODY,
    description=(
        "The task prompt piped to the external agent that explores a repo and "
        "drafts reviewable topic-graph proposals. The flagship external-agent goal."
    ),
    applies_to=("external-agent",),
    variables=(
        PromptVariable("topic_request", "The user's topic request, or the built-in fallback sentence when none was given."),
        PromptVariable("prior_reference", "Regenerate-only block: prior proposal JSON, wiki, and review feedback. Empty on a fresh run.", required=False),
        PromptVariable("custom_instructions", "Rendered `## Custom Instructions` block from the selected prompt-template fragments. Empty when none selected.", required=False),
        PromptVariable("temp_output_path", "Absolute path the agent writes its final JSON to."),
        PromptVariable("output_file", "The canonical artifact path the agent must NOT write directly."),
        PromptVariable("finish_cmd", "The exact CLI command that finalizes the run."),
        PromptVariable("existing_topics_json", "JSON summary of already-approved topics (do-not-duplicate list)."),
        PromptVariable("buckets_json", "JSON summary of the available top-level navigation buckets."),
        PromptVariable("sibling_section", "The 'Sibling topics being refreshed' block, or empty.", required=False),
    ),
)

__all__ = ["SURFACE_ID"]
