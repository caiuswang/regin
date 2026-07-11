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

from lib.prompts import get_surface
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
    standards = get_surface("topic-authoring-standards").default_body()
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
- `aliases` are *alternate* phrases a future agent might search for — not restatements of the `id` or `label`. Do NOT list the topic id or label, and do NOT add variants that differ only in case, spacing, or hyphenation: regin normalizes aliases (lowercased, every run of non-alphanumeric characters → a single space), so `foo-bar`, `Foo Bar`, and `foo bar` all collapse to the same key and a repeat is rejected at apply time. Give 0–6 genuinely distinct phrasings, or leave the list empty.
- If a write/tool permission prompt blocks writing the output file, stop and report the permission failure instead of printing a fallback success payload.

{standards}

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

Existing approved topics — a boundary map, not their full text. `topics[]` gives each one's bucket (`parent_id`), a one-line `covers`, and the on-disk position of its `wiki_path` / `json_path`: when your topic is adjacent to one, Read that wiki with your Read tool and scope yours to what it does not cover, cross-linking it with `[[id]]` instead of restating it. `primary_owners` maps a file to the ONE topic that already owns it as a primary ref — if you cite such a file, tag it `tier: "reference"` and `[[link]]` its owner rather than claiming a second primary (the same file primary in two topics is a boundary violation that gets a draft bounced before review). Explore the repo with your Read/Glob/Grep tools for everything else:
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
