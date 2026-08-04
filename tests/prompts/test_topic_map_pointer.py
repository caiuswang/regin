"""The drafting surface's boundary map moved from an embedded JSON blob to a
Read pointer (`lib/topics/proposal_external._existing_topics_pointer`).

Embedded, the map was 84% of the rendered prompt (43,020 of 51,467 chars on
regin's own graph) and grew with every approved topic. Two things have to hold
for that to be more than a local edit: the map must reach disk intact, and the
pre-change default's hash must be registered so `seed_builtin_skeletons` heals
an existing install instead of pinning the 43k body forever.
"""

from __future__ import annotations

import json

from lib.prompt_templates import (get_template_by_slug, seed_builtin_skeletons,
                                  update_template)
from lib.prompts import get_surface
import lib.topics.proposal_external as pe

SLUG = "topic-proposal-drafting"

# The drafting surface's last-shipped default verbatim (boundary map embedded
# in a ```json fence) — the body an un-edited pre-upgrade install still stores.
# Its sha256 is registered in lib/prompts/surfaces/drafting.py.
_LAST_EMBEDDED_DEFAULT = """\
# Regin Topic Proposal Agent Task

Inspect this repository as needed and draft reviewable topic graph proposals.

User topic request:
{{topic_request}}{{prior_reference}}{{custom_instructions}}

Rules:
- Do not modify `.regin/topics/topics/` or approved topic data.
- Write final JSON to the temp output file `{{temp_output_path}}`.
- Do not write `{{output_file}}` directly; regin will validate and copy the temp output into that canonical artifact.
- You may also print the same JSON as a fenced `json` block.
- Keep all file paths relative to the repository root.
- `aliases` are *alternate* phrases a future agent might search for — not restatements of the `id` or `label`. Do NOT list the topic id or label, and do NOT add variants that differ only in case, spacing, or hyphenation: regin normalizes aliases (lowercased, every run of non-alphanumeric characters → a single space), so `foo-bar`, `Foo Bar`, and `foo bar` all collapse to the same key and a repeat is rejected at apply time. Give 0–6 genuinely distinct phrasings, or leave the list empty.
- If a write/tool permission prompt blocks writing the output file, stop and report the permission failure instead of printing a fallback success payload.

{{include:topic-authoring-standards}}

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
      "refs": [{"path": "relative/path.py", "role": "implementation"}, {"path": "relative/example.py", "tier": "reference"}],
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

Existing approved topics — a boundary map, not their full text. `topics[]` gives each one's bucket (`parent_id`), a one-line `covers`, and the on-disk position of its `wiki_path` / `json_path`: when your topic is adjacent to one, Read that wiki with your Read tool and scope yours to what it does not cover, cross-linking it with `[[id]]` instead of restating it. `primary_owners` maps a file to the ONE topic that already owns it as a primary ref — if you cite such a file, tag it `tier: "reference"` and `[[link]]` its owner rather than claiming a second primary (the same file primary in two topics is a boundary violation that gets a draft bounced before review). A key ending in `/` is a directory ref: its owner claims every file beneath it, so a file under such a key is owned too. Explore the repo with your Read/Glob/Grep tools for everything else:
```json
{{existing_topics_json}}
```

Available buckets (pick one id for each topic's `parent_id`, or null if none fits):
```json
{{buckets_json}}
```
{{sibling_section}}"""


def _repo_with_topic(tmp_path):
    """A repo whose authoritative graph has one approved topic owning one file,
    so both halves of the map (`topics`, `primary_owners`) are non-empty."""
    repo = tmp_path / "repo"
    tdir = repo / ".regin" / "topics" / "topics"
    tdir.mkdir(parents=True)
    (tdir / "alpha.json").write_text(json.dumps({
        "id": "alpha", "label": "Alpha", "parent_id": "buck",
        "blurb": "the alpha topic", "kind": "topic",
        "refs": [{"path": "lib/alpha.py", "tier": "primary"}],
    }))
    (tdir / "buck.json").write_text(json.dumps({
        "id": "buck", "label": "Bucket", "kind": "bucket", "blurb": "a bucket",
    }))
    return repo


def test_map_spilled_to_disk_matches_the_inline_payload(tmp_path):
    """The pointer target carries exactly what used to be embedded — the
    refactor moves the bytes, it does not thin them."""
    repo = _repo_with_topic(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    pointer = pe._existing_topics_pointer(repo, out_dir)

    written = json.loads((out_dir / pe.TOPIC_MAP_FILE).read_text())
    assert written == pe._existing_topics_summary(repo)
    assert written["topics"], "topic positions must survive the spill"
    assert written["primary_owners"] == {"lib/alpha.py": "alpha"}
    # Absolute: the agent may run under a cwd other than the repo root.
    assert pointer == str((out_dir / pe.TOPIC_MAP_FILE).resolve())


def test_prompt_points_at_the_map_instead_of_embedding_it(tmp_path):
    repo = _repo_with_topic(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    prompt = pe._instructions(repo, "auth", out_dir, out_dir / "tmp.json")

    assert str((out_dir / pe.TOPIC_MAP_FILE).resolve()) in prompt
    # The blob itself is gone: `primary_owners` is a key of the spilled JSON,
    # and the prompt may only *name* it in prose, never carry its contents.
    assert '"lib/alpha.py": "alpha"' not in prompt
    assert "the alpha topic" not in prompt      # a topic's `covers` line
    # ...while the small bucket enum stays inline (a Read round-trip to save
    # ~700 tokens is a worse trade than carrying it).
    assert "a bucket" in prompt


def test_unwritable_out_dir_degrades_to_placeholder(tmp_path, monkeypatch):
    """A failed spill must never leave a dangling path in the prompt — the
    agent would Read nothing and silently draft without the boundary rule."""
    repo = _repo_with_topic(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    def _boom(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr("pathlib.Path.write_text", _boom)

    pointer = pe._existing_topics_pointer(repo, out_dir)

    assert pointer == "(topic map unavailable)"
    assert not (out_dir / pe.TOPIC_MAP_FILE).exists()


def test_registered_retired_hash_heals_an_existing_install(tmp_db):
    """Without the registered hash this refactor is dead code on every install
    that ever seeded: `render_surface` prefers the stored row, so the 43k body
    would be pinned forever."""
    from sqlmodel import select

    from lib.orm import SessionLocal
    from lib.orm.models import PromptTemplate

    seed_builtin_skeletons()
    with SessionLocal() as session:          # verbatim, as the old seeder wrote it
        row = session.exec(select(PromptTemplate)
                           .where(PromptTemplate.slug == SLUG)).one()
        row.body = _LAST_EMBEDDED_DEFAULT
        session.add(row)
        session.commit()

    assert seed_builtin_skeletons() == 1

    healed = get_template_by_slug(SLUG)["body"]
    assert healed == get_surface(SLUG).default_body()
    assert "{{existing_topics_pointer}}" in healed
    assert "{{existing_topics_json}}" not in healed
    assert seed_builtin_skeletons() == 0      # stable; no re-heal loop


def test_a_hand_edited_body_is_never_healed(tmp_db):
    seed_builtin_skeletons()
    update_template(SLUG, {"body": "MY OWN DRAFTING PROMPT {{topic_request}}"})
    assert seed_builtin_skeletons() == 0
    assert get_template_by_slug(SLUG)["body"].startswith("MY OWN")
