"""Characterization test: the editable ``memory-distill`` skeleton renders
byte-identical to the pre-refactor hardcoded distill prompt.

``_reference_compose`` below is a **frozen copy** of what
``lib.memory.distill._compose_prompt`` produced before the dynamic-prompt
refactor — the frozen ``_DISTILL_PROMPT`` / ``_OUTPUT_FORMAT`` literals joined
with the same (unchanged, shared) ``_tagged`` / ``_grade_digest`` /
``_signal_digest`` helpers. The only thing under test is the migrated surface
body + the context wiring in the new ``_compose_prompt``. If the two ever
diverge, the migration dropped or mangled text — edit the surface body
(``lib/prompts/surfaces/memory.py``) and this reference together.
"""

from __future__ import annotations

import lib.memory.distill as distill

# --- Frozen pre-refactor literals (do NOT edit to match the surface) ---------
_FROZEN_DISTILL_PROMPT = """# Distill a coding session into memory lessons

<role>
You distill a finished coding-agent session into a few REUSABLE memories
for future sessions. Your job is to ABSTRACT the transferable rule behind
what went wrong (or a hard-won, non-obvious fact) — never to narrate what
happened.
</role>

<session_id>{trace_id}</session_id>

<gather_evidence>
You have a shell. Investigate the session's own recorded trace — the hints
below only point at where to look; confirm the specifics yourself:
1. Run `{python} cli/regin.py trace dump {trace_id} --index` → JSON with the
   user `prompts`, the `final_deliverable`, `commit_messages`, and a COMPACT
   `spans` catalog (span_id, tool, file_path, command, status, short preview).
2. For any moment worth a rule, run
   `{python} cli/regin.py trace span {trace_id} <span_id>` → that span's full
   content (the exact error text, the diff, the command, the output). Fetch
   sparingly — only the spans a candidate memory needs.
3. When the trace references a commit (e.g. in `commit_messages` or a Bash
   span), you may run `git log --oneline -20` to find nearby commits, and
   `git show <sha>` to read the exact diff. Cite the commit sha when it
   corroborates the rule — e.g. "fixed in <sha>" or "introduced by <sha>".
Use what you read to make each memory concrete and correct — the exact file,
command, error message, commit sha, or symptom — never vague.

Treat the grader's findings below as LEADS, not facts: verify each against the
spans before you abstract a rule from it, and drop any you cannot corroborate
— never invent a rule just to satisfy a finding. The grader can be wrong. For
example, it may flag a file as "read N times" (redundant) when the reads
covered DIFFERENT slices: compare each Read span's `start_line`/`num_lines`
(now in the dump) — paging through a large file in chunks, or re-reading a
file that was edited in between, is not a redundant re-read.
</gather_evidence>

<what_to_capture>
Propose ONLY entries that clear this bar — returning zero or one is a good
outcome, not a failure:
  * a mistake actually made in this session that could easily be made again
    (the symptom, the root cause, and the fix), or
  * a non-obvious, concrete fact or procedure a future session would
    otherwise have to rediscover the hard way.

Write the RULE, not the episode. Strip everything specific to this run — the
session id, "then it succeeded", a blow-by-blow of the attempts:
  BAD  (running account): "Edit on lib/foo.py failed then succeeded on a
        retry in session abc123."
  GOOD (reusable rule):   "Edit fails with 'file has not been read' when the
        file wasn't Read first this session — Read before Edit."
  BAD  (raw utterance):   "User said 'no, use the venv'."
  GOOD (durable rule):    "Run regin via .venv/bin/python; the system
        interpreter lacks the project deps and fails with ImportError."

Do NOT propose generic process advice ("plan before coding", "check existing
code first", "communicate with the user") — anything a capable engineer
already does is noise, not memory. Each entry must be self-contained and
concrete, using the words a future task description would naturally contain
(recall is keyword-matched). Score each entry's `importance` in [0,1] by how
non-obvious, reusable, and likely-to-recur it is — be honest; a marginal note
scores low and is dropped, which is the desired outcome, not a loss.
</what_to_capture>

<doc_redundancy_check>
Before finalising any proposal, check whether the repo's standing
documentation already records the same fact. Every session loads CLAUDE.md
(and AGENTS.md, its mirror) — a proposal that merely restates what those
files say is pure noise; the agent will see it from CLAUDE.md anyway.

Run these checks before proposing (use grep -n so line numbers help you
evaluate how thoroughly the docs cover the fact):
  grep -n "<key phrase>" CLAUDE.md AGENTS.md ARCHITECTURE.md README.md
  grep -rn "<key phrase>" docs/

Drop a candidate when the docs already cover its substance, even if the
wording differs. Propose it only when the docs are silent or when the session
exposed a non-obvious exception, edge-case, or counter-example that a reader
of the docs would not anticipate.

  BAD  (doc-redundant): "Run regin via .venv/bin/python; the system
        interpreter lacks the project deps." — CLAUDE.md § Commands already
        says this verbatim. Every session already knows it.
  GOOD (docs are silent): "regin init silently drops foreign-key violations
        during schema.sql replay; a missing INSERT in db/schema.sql causes
        fresh installs to silently lack the row rather than raising an error."
        — The schema-drift gotcha in CLAUDE.md mentions divergence risk but
        not the silent-drop behavior; this is new information.
</doc_redundancy_check>
"""

_FROZEN_OUTPUT_FORMAT = """<output_format>
After investigating the trace, respond with a JSON array and NOTHING else —
no prose before or after. Each element:
  {"title": "the rule in one line, <= 80 chars (required)",
   "body": "the reusable memory, <= 600 chars",
   "kind": "lesson" | "gotcha" | "procedure" | "preference" | "fact",
   "importance": 0.0-1.0,
   "tags": ["1-3 short keywords"]}
Respond with [] if nothing clears the bar.
</output_format>"""


def _reference_compose(trace_id, spans, grade, python):
    head = _FROZEN_DISTILL_PROMPT.replace("{trace_id}", trace_id).replace(
        "{python}", python)
    sections = [head.rstrip(),
                distill._tagged("grader_findings", distill._grade_digest(grade)),
                distill._tagged("notable_signals", distill._signal_digest(spans)),
                _FROZEN_OUTPUT_FORMAT]
    return "\n\n".join(s for s in sections if s) + "\n"


def _run(trace_id, spans, grade, python=".venv/bin/python"):
    expected = _reference_compose(trace_id, spans, grade, python)
    actual = distill._compose_prompt(trace_id, spans, grade, python)
    return expected, actual


_CORRECTION_SPAN = {"name": "prompt", "span_id": "p1", "start_time": 1.0,
                    "attrs": {"text": "no, that's the wrong file"}}
_GRADE = {"correctness": {"verdict": "FAIL", "detail": {
    "checklist": [{"verdict": "MISSING", "item": "handle the empty case"}]}}}


def test_parity_no_signals_no_grade():
    # Edge case: empty spans + no grade — both digest blocks collapse to nothing.
    expected, actual = _run("abc123", [], None)
    assert actual == expected
    # sanity: no hollow tags when both digests are empty.
    assert "<grader_findings>" not in actual
    assert "<notable_signals>" not in actual


def test_parity_notable_signals_only():
    expected, actual = _run("abc123", [_CORRECTION_SPAN], None)
    assert actual == expected
    assert "<notable_signals>" in actual
    assert "<grader_findings>" not in actual


def test_parity_grade_and_signals():
    expected, actual = _run("abc123", [_CORRECTION_SPAN], _GRADE)
    assert actual == expected
    assert "<grader_findings>" in actual
    assert "<notable_signals>" in actual
    # sanity: the literal JSON single braces in the output contract survive.
    assert '{"title": "the rule in one line' in actual
