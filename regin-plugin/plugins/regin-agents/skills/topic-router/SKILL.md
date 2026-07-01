---
name: topic-router
description: "Run this before any repo task. Triggers: fix a bug, debug, \"how does X work?\", understand a procedure, explain code, plan a change, implement a feature, refactor, edit, investigate, deep into, analyze. It routes 2-6 keywords through regin's dense pattern index (embedding + cross-encoder rerank) so the matching SKILL.md loads before source inspection, lint changes, or tests run."
---

# Topic Router

Every agent must use this skill before broad source exploration or edits in any repository. It routes the task through regin's **dense pattern index** (embedding retrieval + cross-encoder rerank) so the highest-signal pattern guidance is loaded before you touch the code.

## When to invoke (always)

Run this skill at the start of **every** non-trivial repo turn, including:

- **Bug fixes / debugging** — "fix X", "X is broken", "why does X fail", investigating a stack trace, reproducing an issue.
- **Procedure / "how does it work" questions** — "how does X work", "what's the flow for Y", "explain the Z pipeline", "where is X handled".
- **Code edits & refactors** — adding a feature, renaming, splitting, extracting, restructuring.
- **Planning** — before drafting an implementation plan, before entering plan mode, before designing a change.
- **Exploration** — when you'd otherwise reach for `grep`/`find`/`ls` to orient yourself.

Skip only for trivial one-shot tasks where no procedure could apply (e.g. "echo this string", "what does `ls -la` mean").

## Workflow

1. Extract 2-6 concise search keywords from the user's request, changed files, repo area, feature name, or workflow name. Prefer stable nouns and domain terms; do not paste the full goal sentence — dense rerank works best on tight lexical queries.

   Example: use `topics workspace delete`, not `I need to improve the delete flow in the topics workspace`.

2. Route those keywords through the dense pattern index:

   ```bash
   regin route "<keywords>" --top-k 5 --json
   ```

   Note: this command is marked EXPERIMENTAL upstream; output schema may change.

3. If the top result is weak (see "Score interpretation" below) or off-topic, retry once or twice with a narrower keyword set or an adjacent domain term — do not fall back to a full natural-language sentence.

4. Read the `file_path` of the top result(s) — that is the SKILL.md of the matching pattern. Use its body as authoritative procedural guidance for the task at hand.

5. If multiple results have comparable scores (gap < ~0.2), open the top 2-3 and combine. If only the #1 result has a high score and the rest drop sharply, use #1 alone.

6. If no result clears the relevance threshold, proceed to ordinary source exploration — but say explicitly that no pattern matched, so the user knows you are working without curated guidance.

## Expected Route Output

Each result is a JSON object with:

- `slug` — the pattern's short identifier (for wikis: `wiki/<repo>/<topic-id>`).
- `title` — display title.
- `category` — usually `procedure` for patterns, `wiki` for wiki pages.
- `file_path` — path to the matched document. For patterns: the pattern's `SKILL.md` (relative to the regin checkout; absolute location is `$PATTERNS_DIR/<slug>/SKILL.md`, also deployed at `~/.claude/skills/<slug>/SKILL.md`). For wikis: the absolute path to `<repo>/.regin/topics/wiki/<topic-id>.md`. **Read this file** — it carries the actual narrative.
- `source_kind` — `pattern` (user-authored procedure guides; global) or `wiki` (per-topic narrative written when a topic-proposal was accepted; scoped to one repo). Treat wiki results as the most repo-specific guidance available: if the query maps to a known approved topic in this repo, the wiki body usually beats any generic pattern.
- `repo_name` — for `source_kind=wiki`, the registered repo that owns the wiki page (e.g. `regin`). `null` for patterns.
- `header` — only present on wiki results. One-line topic context sourced from the live approved graph at query time: `Topic: <id> | Repo: <name> | Intent: <one-liner> | Refs: <paths>`. The on-disk wiki body is plain markdown with no frontmatter — the header is metadata the wiki itself doesn't carry, so **always read the header alongside the body** when reasoning about a wiki result; the `Refs:` list is the authoritative list of source files the topic covers, and the `Intent:` line is the topic's elevator pitch.
- `score` — float in `[0, 1]`. After rerank this is the cross-encoder confidence.
- `score_kind` — `rerank` (default) or `dense` if `--no-rerank` was passed.

## Score Interpretation

With the default rerank stage:

- **≥ 0.7** — strong match. Trust the pattern's guidance for this task.
- **0.3 – 0.7** — plausible match. Read the pattern body to confirm relevance before applying.
- **< 0.3** — weak. Retry with different keywords; if still weak, treat as unmatched.

The gap between #1 and #2 matters as much as the absolute score. A 0.99 #1 next to a 0.03 #2 is an unambiguous hit; two results at 0.4 each means the query is ambiguous — narrow the keywords.

## Tuning Flags

- `--top-k N` — number of returned results (default 5).
- `--retrieval-k N` — pool size before rerank (default 20). Raise if recall feels low.
- `--no-rerank` — skip the cross-encoder, return raw dense similarities. Faster but noisier; use only when iterating on keywords.
- `--kinds pattern,wiki` — restrict results to one or more `source_kind` values. Default returns both. Use `--kinds wiki` when you specifically want repo-narrative pages and not generic procedure guides; use `--kinds pattern` to ignore wiki noise.
- `--repo NAME` — narrow wiki results to one registered repo (e.g. `--repo regin`). Patterns are global and pass through this filter unchanged — repo-scope only narrows wikis.

## Anti-Patterns

- Do not start broad source exploration before running `regin route`.
- Do not paste a full sentence as the query; dense rerank rewards stable keyword phrases.
- Do not silently fall back to source exploration on a weak match — tell the user no pattern matched.
- Do not edit a pattern's `SKILL.md` as a side effect of applying it; pattern edits go through the regin web UI or `regin pattern` commands.
