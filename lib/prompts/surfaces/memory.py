"""Stage 2 surface registrations for regin's memory-subsystem agent prompts.

Each hardcoded prompt in ``lib/memory/`` — the distiller, the topic classifier,
the recall query-expander, and the reflect dream (the single consolidation
stage) — is migrated here into a ``{{var}}``-placeholder default body,
registered as an editable *surface*, and its call site rewired to
``render_surface``.

Single-brace tokens — IMPORTANT
-------------------------------
The original builders substituted their dynamic values with ``str.replace()``
over **single-brace** tokens (``{trace_id}``, ``{python}``, ``{taxonomy}``,
``{entries}``). Those become engine ``{{double_brace}}`` placeholders here. The
prompt engine treats ONLY ``{{ … }}`` as a slot, so the literal single-brace
JSON examples in these bodies (``{"title": …}``) pass through untouched — they
must NOT be turned into placeholders.

Each body is kept byte-identical to the old composed prompt; characterization
tests in ``tests/prompts/`` assert ``render_surface`` == the frozen reference.
Edit a body here and its parity test together.
"""

from __future__ import annotations

from lib.prompts.registry import (PromptVariable, register_retired_default,
                                  register_surface)

DISTILL_SURFACE_ID = "memory-distill"
TOPIC_CLASSIFY_SURFACE_ID = "memory-topic-classify"
EXPAND_SURFACE_ID = "memory-expand"
DREAM_SURFACE_ID = "memory-reflect-dream"
RETITLE_SURFACE_ID = "memory-retitle"


# --- Memory distiller (lib/memory/distill.py::_compose_prompt) ---------------
# The old builder joined a static head (`_DISTILL_PROMPT`, with {trace_id} /
# {python} replaced) + optional <grader_findings> / <notable_signals> digest
# blocks + a static `_OUTPUT_FORMAT`. Here the head and output contract are the
# static body; `grader_block` / `notable_block` carry their own leading "\n\n"
# separators (empty when the digest is empty) so the spacing is byte-identical.
_DEFAULT_BODY_DISTILL = """<role>
You distill a finished coding-agent session into a few REUSABLE memories
for future sessions. Your job is to ABSTRACT the transferable rule behind
what went wrong (or a hard-won, non-obvious fact) — never to narrate what
happened.
</role>

<session_id>{{trace_id}}</session_id>

<gather_evidence>
You have a shell. Investigate the session's own recorded trace — the hints
below only point at where to look; confirm the specifics yourself:
1. Run `{{python}} cli/regin.py trace dump {{trace_id}} --index` → JSON with the
   user `prompts`, the `final_deliverable`, `commit_messages`, and a COMPACT
   `spans` catalog (span_id, tool, file_path, command, status, short preview).
2. For any moment worth a rule, run
   `{{python}} cli/regin.py trace span {{trace_id}} <span_id>` → that span's full
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
</doc_redundancy_check>{{grader_block}}{{notable_block}}

<output_format>
After investigating the trace, respond with a JSON array and NOTHING else —
no prose before or after. Each element:
  {"title": "the rule in one line, <= 80 chars (required)",
   "body": "the reusable memory, <= 600 chars",
   "kind": "lesson" | "gotcha" | "procedure" | "preference" | "fact",
   "importance": 0.0-1.0,
   "tags": ["1-3 short keywords"]}
Respond with [] if nothing clears the bar.
</output_format>
"""


# --- Topic classifier (lib/memory/topic_classify.py::_compose_prompt) --------
# Old builder: `_PROMPT_HEAD` (with {taxonomy} replaced) + "\n\n" +
# `_memories_block(batch)` + "\n\n" + `_OUTPUT_FORMAT` + "\n".
_DEFAULT_BODY_TOPIC_CLASSIFY = """You are classifying agent-memory entries onto a repo's topic taxonomy.

For each memory below, choose the topic node(s) it is genuinely ABOUT — the
subject of the lesson/gotcha/fact it teaches. Rules:
- Classify on the memory's SUBJECT, never on an incidental file path it mentions.
  A shared cross-cutting infra file (db/schema.sql, hook_manager/core.py,
  lib/skills/skill_router.py) appears across many memories and is NOT evidence
  that a memory is about that file's topic.
- Most memories map to exactly ONE topic. Add a SECOND (rarely a third) only
  when the memory genuinely teaches about two subsystems.
- Prefer the most SPECIFIC topic. A node tagged [category] is a broad
  container — pick it only when no specific child fits. NEVER return both a
  category and one of its children for the same memory; choose only the child.
- If no topic is genuinely related, return an empty list for that memory.
  Do not force a match.
- Use only topic ids from the taxonomy; never invent an id.

<taxonomy>
{{taxonomy}}
</taxonomy>

{{memories_block}}

<output_format>
Respond with ONLY a JSON array, one object per memory you were given:
  [{"id": "<the memory id>", "topics": ["<topic-id>", ...]}, ...]
Use an empty list for a memory with no genuinely related topic. Include every
memory id exactly once.
</output_format>
"""


# --- Recall query expansion (lib/memory/expand.py::_build_prompt) ------------
# Old builder: f"{_INSTRUCTION}\n\nRequest: {query}" (no trailing newline).
_DEFAULT_BODY_EXPAND = (
    "You rewrite a terse coding-session request into a short, keyword-rich "
    "search query for retrieving relevant past engineering lessons. Expand "
    "abbreviations, name the likely technical subsystems, concepts, and "
    "failure modes the request implies. Preserve the original intent; do "
    "not answer the request or invent specifics not implied by it. Output "
    "ONLY the expanded query as 1-2 sentences, no preamble or quoting."
    "\n\nRequest: {{query}}"
)


# --- Reflect dream (lib/memory/reflect.py::_dream) ---------------------------
# The single consolidation LLM stage: ONE call per reflect run decides every
# working row's fate, judges every suspect pair, and may propose a synthesis.
# The evidence pack is built mechanically and bounded; the agent may pull
# deeper evidence via the read-only memory CLI (`dream_allowed_tools`).
_DEFAULT_BODY_DREAM = """<role>
You are the consolidation ("dream") pass over a coding agent's cross-session
memory store. In ONE pass you decide the fate of each fresh working-tier
memory, judge each suspect pair for contradiction, and may abstract a
genuinely shared principle. Be conservative: memories steer future sessions,
and a wrong retirement or a forced abstraction is worse than waiting a cycle.
</role>

<working_memories>
{{working_block}}
</working_memories>

<suspect_pairs>
{{pairs_block}}
</suspect_pairs>

<gather_evidence>
You have a shell with read-only memory commands. The pack above is clipped;
pull deeper evidence sparingly when a decision genuinely hinges on it:
  {{python}} cli/regin.py memory recall "<what a future task would ask>"
  {{python}} cli/regin.py memory list --scope "<scope>" --sort importance
</gather_evidence>

<decisions>
For EVERY working memory choose exactly one:
- promote: a durable, reusable rule worth keeping permanently (episodic).
- hold: real but unproven or too raw — keep it working one more cycle.
  Choose this whenever you are unsure.
- drop: redundant, one-off, or low-value — not worth keeping.
- merge: says essentially what one listed memory already says; set "keeper"
  to that memory's id.
For EVERY suspect pair choose exactly one (OLDER vs NEWER by recorded time):
- contradict: they make incompatible claims about the same thing.
- obsolete: the NEWER describes a later change, fix, or removal that
  supersedes what the OLDER records.
- distinct: neither — both stand. Choose this whenever you are unsure.
Optionally add synthesize actions, ONLY when at least 3 listed entries
genuinely instantiate ONE transferable principle — never force an
abstraction from merely co-retrieved entries; proposing no synthesis is the
normal outcome.
</decisions>

<output_format>
Respond with ONE JSON object and NOTHING else — no prose before or after:
  {"actions": [
    {"action": "promote|hold|drop|merge", "id": "<working memory id>",
     "keeper": "<listed memory id, merge only>", "rationale": "<= 120 chars"},
    {"action": "contradict|obsolete|distinct", "older": "<id>",
     "newer": "<id>", "rationale": "<= 120 chars"},
    {"action": "synthesize", "source_ids": ["<id>", "..."],
     "title": "the principle in one line, <= 80 chars",
     "body": "the reusable rule, <= 600 chars", "rationale": "<= 120 chars"}
  ]}
Include exactly one action per working memory and one per suspect pair.
</output_format>
"""


# --- Title distiller (lib/memory/retitle.py::_compose_prompt) ----------------
# Each lesson below was captured without an explicit title, so it currently
# carries a truncated slice of its own first line as a placeholder. Re-derive
# a real one-line rule from the body — the headline recall, lists, and the
# topic tree key off, and the text the ranker matches queries against.
_DEFAULT_BODY_RETITLE = (
    "Each block below is a coding-session lesson whose TITLE is missing — it "
    "currently holds a truncated slice of the body as a placeholder. Write a "
    "better title for each: the ONE rule the lesson teaches, stated as a "
    "single imperative line a future session can scan (\"Restart vite after "
    "proxy edits\", not \"Vite issue\"). Concrete over vague; <= 80 chars; no "
    "trailing ellipsis; do not just copy the body's first line.\n\n"
    "Respond with a JSON array and NOTHING else — one object per block, "
    "keyed by its `i`:\n"
    '  [{"i": 0, "title": "the rule in one line, <= 80 chars"}, ...]\n\n'
    "{{entries}}"
)


register_surface(
    DISTILL_SURFACE_ID,
    label="Memory — session distiller",
    area="memory",
    default_body=_DEFAULT_BODY_DISTILL,
    tags=("memory", "distill"),
    description=(
        "The agentic prompt that distills a finished session's trace into "
        "reusable memory proposals (`lib/memory/distill.py`). The agent "
        "self-fetches spans; the grader/notable-signals hints are spliced in."
    ),
    applies_to=("memory",),
    variables=(
        PromptVariable("trace_id", "The session trace id the distiller investigates."),
        PromptVariable("python", "The interpreter the agent invokes regin's CLI with (e.g. `.venv/bin/python`)."),
        PromptVariable("grader_block", "The `<grader_findings>` digest block (with its leading separator), or empty when the grader flagged nothing.", required=False),
        PromptVariable("notable_block", "The `<notable_signals>` digest block (with its leading separator), or empty when no heuristic signal fired.", required=False),
    ),
)

register_surface(
    TOPIC_CLASSIFY_SURFACE_ID,
    label="Memory — topic classifier",
    area="memory",
    default_body=_DEFAULT_BODY_TOPIC_CLASSIFY,
    tags=("memory", "topic-classify"),
    description=(
        "The prompt that classifies a batch of memories onto a repo's topic "
        "taxonomy nodes (`lib/memory/topic_classify.py`)."
    ),
    applies_to=("memory",),
    variables=(
        PromptVariable("taxonomy", "The one-line-per-node taxonomy digest the model classifies against."),
        PromptVariable("memories_block", "The batch rendered as `<memory id=…>` blocks (id, title, clipped body)."),
    ),
)

register_surface(
    EXPAND_SURFACE_ID,
    label="Memory — recall query expansion",
    area="memory",
    default_body=_DEFAULT_BODY_EXPAND,
    tags=("memory", "recall-expand"),
    description=(
        "The prompt that rewrites a terse recall request into a keyword-rich "
        "search query (`lib/memory/expand.py`)."
    ),
    applies_to=("memory",),
    variables=(
        PromptVariable("query", "The raw recall request to expand."),
    ),
)

register_surface(
    DREAM_SURFACE_ID,
    label="Memory — reflect dream",
    area="memory",
    default_body=_DEFAULT_BODY_DREAM,
    tags=("memory", "dream"),
    description=(
        "The single reflect consolidation stage: one agentic call per run "
        "that decides every working row's fate (promote/hold/drop/merge), "
        "judges every suspect pair (contradict/obsolete/distinct), and may "
        "propose a synthesis (`lib/memory/reflect.py::_dream`)."
    ),
    applies_to=("memory",),
    variables=(
        PromptVariable("working_block", "Every pending working-tier memory with its top co-retrieval neighbours."),
        PromptVariable("pairs_block", "The suspect episodic pairs (shared referent, same scope), OLDER/NEWER labeled."),
        PromptVariable("python", "The interpreter the agent invokes regin's CLI with (e.g. `.venv/bin/python`)."),
    ),
)

register_surface(
    RETITLE_SURFACE_ID,
    label="Memory — title distiller",
    area="memory",
    default_body=_DEFAULT_BODY_RETITLE,
    tags=("memory", "retitle"),
    description=(
        "Re-derives a proper one-line rule TITLE for lessons that were "
        "captured without one (a truncated body-slice placeholder). Batched "
        "bodies in, JSON titles out (`lib/memory/retitle.py`)."
    ),
    applies_to=("memory",),
    variables=(
        PromptVariable("entries", "The lessons to retitle, one `<lesson i=n>…</lesson>` block each."),
    ),
)

# Superseded default bodies (sha256), so the seeder can heal un-edited stale
# rows — or DELETE them when the slug itself was retired (all four reflect
# micro-stage surfaces were folded into the single dream stage; a builtin row
# still carrying one of these default bodies is dead seed data, while a
# user-edited row for a dead slug is kept). Two generations per slug where a
# revision shipped between the original and the retirement.
_RETIRED_SLUG_HASHES = {
    "memory-reflect-contradiction": (
        # original 2-way CONTRADICT/DISTINCT prompt
        "9f22fc1136962d500a1b658d556127ed7405aee8506dd477b193f58cc3187c16",
        # 3-way CONTRADICT/OBSOLETE/DISTINCT revision
        "bce549d97d6aa29506c2359be25a0e501c5af1f578f8f9bd88d84be6c13bed40",
    ),
    "memory-reflect-digest": (
        # original scope-blind briefing prompt
        "90a21ccd8822e0465fbf10eaccd1b8255d35899c6987469f3e73f7dfffe2333c",
        # scope-aware revision
        "8c252b895f57c5f1b14010cb4dfbe60555a083cada7f716f7fb6b089d5caebc7",
    ),
    "memory-reflect-promote": (
        "a73f90b41556790ca921812f2e77a6b642b6a8b280831bec1500a2c248b39f60",
    ),
    "memory-reflect-synthesis": (
        "b6dbf2efd0526c9c1d9136d72d68ac0d96bce541c174f7c0448d1cdfa371bc98",
    ),
}
for _slug, _hashes in _RETIRED_SLUG_HASHES.items():
    for _sha in _hashes:
        register_retired_default(_slug, sha256=_sha)

__all__ = [
    "DISTILL_SURFACE_ID",
    "DREAM_SURFACE_ID",
    "EXPAND_SURFACE_ID",
    "RETITLE_SURFACE_ID",
    "TOPIC_CLASSIFY_SURFACE_ID",
]
