"""Characterization test: the editable drafting skeleton renders byte-identical
to the pre-refactor hardcoded f-string.

``_reference_instructions`` below is a **frozen copy** of what
``proposal_external._instructions`` produced before the dynamic-prompt-template
refactor. It calls the same (unchanged, shared) helper functions, so the only
thing under test is the migrated template body + the context wiring in the new
``_instructions``. If the two ever diverge, the migration dropped or mangled
text — edit the surface body and this reference together.
"""

from __future__ import annotations

import json

import lib.topics.proposal_external as pe


def _reference_instructions(repo, topic_request, out_dir, temp_output_path,
                            prior_draft=None, prompt_templates=None):
    prior_reference = ""
    if prior_draft:
        feedback_reference = ""
        feedback_block = pe.format_review_feedback_for_prompt(prior_draft.get("feedback_threads") or [])
        if feedback_block:
            feedback_reference = f"""
{feedback_block}

"""
        prior_reference = f"""

Prior draft reference:
{feedback_reference}Use the previous proposal and wiki only as reference — to keep good coverage and to address any review feedback above — not as a baseline to diff against. Re-check every topic against the current repository.

Write each topic's wiki and the notes as a standalone description of the repository as it is NOW. Do NOT write changelog or diff prose comparing this revision to the previous one: avoid phrasing like "was removed", "is now", "no longer", "the old …", "previously", "changed from", or "renamed to". The reader has never seen the prior draft — describe the current structure and behavior directly, citing files that exist today.

A regenerate REVISES the page in place; it does not accrete. Keep each wiki's scope and length close to the prior draft — correct what the current code no longer matches and cut detail that has gone stale, rather than appending new file-by-file descriptions because some files changed. A drift note asking you to refresh a topic is a request to re-verify and tighten its existing narrative, not to grow it.

Previous proposal JSON:
```json
{json.dumps(pe._prior_proposal_for_prompt(prior_draft.get('proposal')), indent=2, sort_keys=True)}
```

Previous wiki markdown:
```markdown
{str(prior_draft.get('wiki') or '')}
```
"""
    custom = pe._format_template_section(prompt_templates)
    sibling_section = pe._sibling_refresh_section(repo, out_dir)
    finish_cmd = pe._finish_command(repo, out_dir.name)
    return f"""# Regin Topic Proposal Agent Task

Inspect this repository as needed and draft reviewable topic graph proposals.

User topic request:
{topic_request or "No specific topic request was provided. Propose the most useful uncovered topics from the repository."}{prior_reference}{custom}

Rules:
- Do not modify `.regin/topics/topic.json` or approved topic data.
- Write final JSON to the temp output file `{temp_output_path}`.
- Do not write `{out_dir / pe.OUTPUT_FILE}` directly; regin will validate and copy the temp output into that canonical artifact.
- You may also print the same JSON as a fenced `json` block.
- Keep all file paths relative to the repository root.
- Only propose topics justified by real repository files; every ref path must exist in the repo (regin rejects paths it can't find on disk).
- A ref's `role` is optional; when you set one, use only: overview, architecture, entrypoint, api, schema, test, migration, implementation, config, docs. Omit it if none clearly fits.
- A ref's `tier` is optional and orthogonal to `role`: it says how central the file is to THIS wiki. Use `"reference"` for a file the wiki only points at as context or an example and does NOT explain in detail — those are excluded from content-drift, so a later edit to them won't nag for a wiki refresh. Omit `tier` (or set `"primary"`) for a file the wiki actually describes. When in doubt, omit it (defaults to primary). Tag reference-only files generously — it is the main lever for keeping this wiki's drift low.
- A topic `wiki` is a durable conceptual overview, not a file-by-file catalog: explain the topic's purpose, mental model, main flows, and the invariants/gotchas that outlast the code, and cite specific files or functions only where they anchor that narrative. Keep it tight — a well-scoped page stays roughly the same length as the topic evolves. Do not try to describe every ref; pointer-only (`tier: "reference"`) files need no prose.
- `aliases` are *alternate* phrases a future agent might search for — not restatements of the `id` or `label`. Do NOT list the topic id or label, and do NOT add variants that differ only in case, spacing, or hyphenation: regin normalizes aliases (lowercased, every run of non-alphanumeric characters → a single space), so `foo-bar`, `Foo Bar`, and `foo bar` all collapse to the same key and a repeat is rejected at apply time. Give 0–6 genuinely distinct phrasings, or leave the list empty.
- `parent_id` places the topic under one top-level navigation bucket (see "Available buckets" below). Pick the single best-fitting bucket id. If none clearly fits, set it to `null` — the reviewer will place it; do NOT force a weak fit. `blurb` is a one-line router card ("what task should drill in here"), not a description; omit it and `intent` is used instead.
- Every topic MUST include its own `wiki`: a self-contained Markdown page describing THAT topic — its files, behavior, and how it fits — because each topic becomes a separate `.regin/topics/wiki/<id>.md` page. Do NOT write one combined document covering every topic; give each topic its own distinct page. Put any shared framing that spans topics in the top-level `overview` instead, not repeated into each topic's wiki.
- If a write/tool permission prompt blocks writing the output file, stop and report the permission failure instead of printing a fallback success payload.

Signal completion (REQUIRED — do this LAST):
- After you have written the JSON to the temp output file, run this exact command to ingest your proposal and mark this run complete. It is the ONLY thing that finalizes the run — if you skip it, the run is treated as failed:

  {finish_cmd}

- The same command is available in the `REGIN_TOPIC_PROPOSAL_FINISH_CMD` environment variable. Run it once, as your final action, after the output file exists. Do not run it before the file is written.

Output JSON shape:
{{
  "topics": [
    {{
      "id": "short-stable-id",
      "label": "Human label",
      "aliases": [],
      "intent": "What this topic helps future agents understand",
      "status": "active",
      "parent_id": "one-of-the-bucket-ids-below-or-null",
      "blurb": "One line: what task should drill into this topic",
      "refs": [{{"path": "relative/path.py", "role": "implementation"}}, {{"path": "relative/example.py", "tier": "reference"}}],
      "edges": [],
      "commands": [],
      "include_globs": ["path/**"],
      "exclude_globs": [],
      "evidence_paths": ["relative/path.py"],
      "wiki": "Markdown wiki page for THIS topic only — its own standalone narrative"
    }}
  ],
  "notes": [],
  "overview": "Optional short markdown intro tying the proposed topics together"
}}

Existing approved topics — each entry's `covers` and `wiki_sections` show the territory it already owns. Do NOT propose a topic that duplicates or substantially restates one of these; when your topic is adjacent, scope it to what they don't cover and cross-link the sibling with `[[id]]` instead of re-explaining it. Explore the repo with your Read/Glob/Grep tools for everything else:
```json
{json.dumps(pe._existing_topics_summary(repo), indent=2, sort_keys=True)}
```

Available buckets (pick one id for each topic's `parent_id`, or null if none fits):
```json
{json.dumps(pe._bucket_summary(repo), indent=2, sort_keys=True)}
```
{sibling_section}"""


def _run(repo, out_dir, **kw):
    temp_output = out_dir / "topics.tmp.json"
    expected = _reference_instructions(repo, kw.get("topic_request"), out_dir, temp_output,
                                       kw.get("prior_draft"), kw.get("prompt_templates"))
    actual = pe._instructions(repo, kw.get("topic_request"), out_dir, temp_output,
                              prior_draft=kw.get("prior_draft"),
                              prompt_templates=kw.get("prompt_templates"))
    return expected, actual


def test_parity_no_request_no_templates(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    out_dir = tmp_path / "out"; out_dir.mkdir()
    expected, actual = _run(repo, out_dir)
    assert actual == expected


def test_parity_with_request_and_templates(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    out_dir = tmp_path / "out"; out_dir.mkdir()
    templates = [
        {"slug": "g", "label": "GitNexus", "body": "Use gitnexus. Literal brace: {x}."},
        {"slug": "h", "label": "House style", "body": "Prefer terse wikis."},
    ]
    expected, actual = _run(repo, out_dir, topic_request="auth and sessions",
                            prompt_templates=templates)
    assert actual == expected
    # sanity: the literal single brace inside a fragment survives substitution
    assert "Literal brace: {x}." in actual


def test_parity_with_prior_draft(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    out_dir = tmp_path / "out"; out_dir.mkdir()
    prior = {
        "proposal": {"version": 1, "topics": [{"id": "t1", "label": "T1"}]},
        "wiki": "# Old wiki\ncontent",
        "feedback_threads": [
            {"topic_id": "t1", "anchor": "intent", "quoted_text": "vague",
             "comments": [{"author": "rev", "body": "sharpen this"}]},
        ],
    }
    expected, actual = _run(repo, out_dir, topic_request="refresh", prior_draft=prior)
    assert actual == expected
    assert "Prior draft reference:" in actual
