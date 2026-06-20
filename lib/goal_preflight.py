"""Goal preflight — deterministic roadmap router for `/goal-verified`.

Turns a freeform goal string into a *roadmap*: the concrete standards a
build must conform to (skills to read, reference components to mirror,
design tokens to reuse) plus the hard gates that decide "done". This is
the front half of the loop-engineering workflow — the bar is pinned
*before* the agent builds, so verification has something falsifiable to
check against instead of the agent grading its own homework.

Design decision (deliberate): this module is **pure deterministic
routing**, no embeddings and no LLM. Skill/area selection is a *routing*
problem over a small fixed set, not a *retrieval* problem — regin's own
history shows embedding/keyword routing adds ~zero lift here while the
file-keyed convention table in CLAUDE.local.md is 100% precise. The one
irreducibly generative step — turning this scaffold into concrete,
falsifiable acceptance checklist items — is done by the agent that
consumes the roadmap (the `/goal-verified` skill), not here.

The rules table mirrors the pre-edit conventions already enforced by
regin's hooks (RadonEngine, GritEngine, the two bundle engines) so the
roadmap never invents a standard the repo doesn't already hold.
"""

from __future__ import annotations

import fnmatch
import glob
import os
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AreaRule:
    """One row of the routing table.

    An area fires when the goal text mentions any `keyword`, or when it
    names a path matching any `path_globs` entry. A fired area
    contributes its skills/tokens/gates to the roadmap and its
    `reference_globs` are resolved against the repo to surface concrete
    sibling components to mirror.
    """

    name: str
    keywords: tuple[str, ...]
    path_globs: tuple[str, ...]
    skills: tuple[str, ...]
    reference_globs: tuple[str, ...] = ()
    tokens: tuple[str, ...] = ()
    gates: tuple[str, ...] = ()


# --- The routing table -------------------------------------------------
# Keyed on the area a goal touches. Skills + gates intentionally match
# what regin already enforces on PostToolUse so the bar is the repo's
# real bar, not an invented one. Keep keywords lowercase.
AREA_RULES: tuple[AreaRule, ...] = (
    AreaRule(
        name="frontend",
        keywords=("ui", "ux", "vue", "frontend", "component", "view",
                  "page", "style", "css", "tailwind", "button", "filter",
                  "modal", "layout", "dashboard", "screen", "inbox"),
        path_globs=("frontend/*", "*.vue"),
        skills=("vue-complexity", "frontend-style-convention",
                "ui-ux-regin-surfaces", "ui-ux-checklists"),
        reference_globs=("frontend/src/views/*.vue",
                         "frontend/src/components/**/*.vue"),
        tokens=("frontend/src/assets/style.css",),
        gates=(
            "cd frontend && npx vite build",
            "cd frontend && ./node_modules/.bin/playwright test",
            "bundle engines (frontend-style-convention, vue-complexity) clean",
            "zero browser console errors in the changed view",
        ),
    ),
    AreaRule(
        name="python",
        keywords=("python", "cli", "lib", "endpoint", "api", "backend",
                  "orm", "sqlmodel", "hook", "handler", "service"),
        path_globs=("*.py", "lib/*", "cli/*", "web/*"),
        skills=("python-complexity", "regin-python-conventions", "grit-rules"),
        reference_globs=(),
        tokens=(),
        gates=(
            ".venv/bin/python -m pytest (relevant tests pass)",
            "radon grade >= C on changed functions",
            "grit rules clean on changed files",
        ),
    ),
    AreaRule(
        name="trace",
        keywords=("trace", "span", "session", "timeline", "ingest",
                  "conversation card"),
        path_globs=("lib/trace/*", "web/blueprints/trace/*"),
        skills=("python-complexity", "regin-python-conventions"),
        reference_globs=("lib/trace/*.py",),
        tokens=(),
        gates=(
            ".venv/bin/python -m pytest tests/trace (trace tests pass)",
            "verify against the live trace UI (route /trace/sessions/<sid>)",
        ),
    ),
    AreaRule(
        name="docs",
        keywords=("readme", "documentation", "docs", "guide", "wiki page"),
        path_globs=("*.md", "docs/*"),
        skills=("doc-hygiene",),
        reference_globs=(),
        tokens=(),
        gates=("markdown renders; links resolve",),
    ),
)

# Gates that apply to every goal regardless of area — the universal
# "no-sayer" floor.
BASE_GATES: tuple[str, ...] = (
    "existing test suite stays green",
    "an independent reviewer (fresh context) checked the diff — /code-review high",
)

# Cap on how many reference components to surface per area, so the
# roadmap stays a short menu, not a file dump.
_MAX_REFERENCES = 6

# Minimum distinct keyword hits an area needs to fire on keywords alone.
# A single lone keyword (e.g. an incidental "session") is too weak a
# signal and pulls in a whole area's skills/refs/gates as noise; require
# corroborating evidence. A path-glob signal still fires an area on its
# own (see detect_areas).
_MIN_KEYWORD_HITS = 2


@dataclass
class Roadmap:
    """The assembled, pre-build bar for a goal."""

    goal: str
    areas: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)
    gates: list[str] = field(default_factory=list)
    # Recalled lessons (Slice 2). Each: {id, title, snippet}. Populated
    # only when build_roadmap(with_lessons=True); empty otherwise so the
    # deterministic core stays pure and offline.
    lessons: list[dict] = field(default_factory=list)


# How many past lessons to surface in a roadmap — a short menu the agent
# folds the relevant ones into its checklist, not a dump.
_MAX_LESSONS = 5


def recall_lessons(goal: str, areas: list[str], *, limit: int = _MAX_LESSONS,
                   scope: str | None = None) -> list[dict]:
    """Best-effort recall of past lessons relevant to a goal (Slice 2 front).

    Reuses the existing memory store's `recall` (FTS-capable, no embedder
    required) rather than any new index. Area names bias ordering: a hit
    tagged with a triggered area floats above generic hits. Returns compact
    dicts carrying the memory **id**, so the consuming skill can later
    report which lessons made it into the approved roadmap — that
    inclusion is the clean engagement signal `goal feedback` records.

    Degrades to `[]` on any failure (memory disabled, store missing, import
    error) so preflight never breaks just because memory is off.
    """
    try:
        import lib.memory as memory
        if not memory.enabled():
            return []
        # FTS mode keeps preflight fast and offline — no dense-model load on
        # the hot path (mirrors the auto-inject hook's FTS-only contract).
        # reinforce=False is load-bearing: merely *offering* a lesson in a
        # roadmap is not the agent *using* it. recall_count is reserved for
        # deliberate use (a lesson folded into the approved roadmap, credited
        # by `goal feedback --included`); letting preflight bump it would
        # inflate the very usefulness signal Slice 2 measures.
        hits = memory.recall(goal, top_k=max(limit * 2, limit), mode="fts",
                             reinforce=False)
    except Exception:
        return []

    area_set = {a.lower() for a in areas}

    def _area_rank(hit) -> int:
        tags = {str(t).lower() for t in (hit.memory.get("tags") or [])}
        return 0 if (tags & area_set) else 1

    ordered = sorted(hits, key=_area_rank)
    out: list[dict] = []
    for hit in ordered[:limit]:
        mem = hit.memory
        body = (mem.get("body") or "").strip().replace("\n", " ")
        out.append({
            "id": mem.get("id"),
            "title": mem.get("title") or "",
            "snippet": body[:160],
        })
    return out


def _normalize(text: str) -> str:
    return text.lower()


def _keyword_hits(goal_lc: str, rule: AreaRule) -> int:
    """Count distinct keywords of `rule` that appear as whole words."""
    return sum(1 for kw in rule.keywords
               if re.search(rf"\b{re.escape(kw)}\b", goal_lc))


def _mentions_path(goal_lc: str, rule: AreaRule) -> bool:
    # A path glob fires if the goal literally names a matching token.
    tokens = re.split(r"[\s,]+", goal_lc)
    return any(fnmatch.fnmatch(tok, pat)
               for tok in tokens for pat in rule.path_globs)


def _fires(goal_lc: str, rule: AreaRule) -> bool:
    """An area fires on a path-glob signal, or on enough keyword evidence.

    Routing requires *strong* evidence: either the goal literally names a
    path matching the area's `path_globs`, or it hits at least
    `_MIN_KEYWORD_HITS` distinct keywords. A single lone keyword is too
    weak to fire an area on its own.
    """
    if _mentions_path(goal_lc, rule):
        return True
    return _keyword_hits(goal_lc, rule) >= _MIN_KEYWORD_HITS


def detect_areas(goal: str) -> list[AreaRule]:
    """Return the area rules a goal triggers, in table order.

    Pure lexical routing: an area fires on a path-glob hit or on at least
    `_MIN_KEYWORD_HITS` distinct keyword hits. Order is preserved so
    output is stable/deterministic.
    """
    goal_lc = _normalize(goal)
    return [r for r in AREA_RULES if _fires(goal_lc, r)]


def record_offered(session_id: str | None, lessons: list[dict],
                   goal: str) -> int:
    """Persist that `lessons` were *offered* to `session_id` (Slice 2).

    Makes the engagement *denominator* automatic: even if a run never calls
    `goal feedback`, the store still knows which lessons a roadmap surfaced,
    so reflect's decay half can see "offered many times, never used → fade".
    Reuses the store's `record_injections` — which logs an injection event
    WITHOUT bumping recall_count, so this records exposure, not usefulness.

    Best-effort: needs a session id (none in a CLI subprocess's env, so the
    caller passes `--session-id`); returns 0 and does nothing if absent,
    memory is disabled, or anything fails — preflight must never break on it.
    """
    if not session_id or not lessons:
        return 0
    try:
        import lib.memory as memory
        if not memory.enabled():
            return 0
        ids = [le["id"] for le in lessons if le.get("id")]
        if not ids:
            return 0
        memory.get_store().record_injections(session_id, ids, query=goal)
        return len(ids)
    except Exception:
        return 0


def _ident_tokens(name: str) -> set[str]:
    """Lowercase word tokens of an identifier, splitting camelCase and
    PascalCase so `InboxView` → {inbox, view} (not one opaque token)."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return set(re.findall(r"[a-z]+", spaced.lower()))


def _rank_references(paths: list[str], goal: str) -> list[str]:
    """Order candidate files by filename-token overlap with the goal so
    the most topically-relevant siblings surface first."""
    goal_tokens = _ident_tokens(goal)

    def overlap(path: str) -> int:
        stem = os.path.splitext(os.path.basename(path))[0]
        return len(_ident_tokens(stem) & goal_tokens)

    return sorted(paths, key=lambda p: (-overlap(p), p))


def resolve_references(rule: AreaRule, goal: str, repo_root: str) -> list[str]:
    """Glob an area's reference components against the repo and rank them
    by relevance to the goal. Deterministic; capped to keep it a menu."""
    found: list[str] = []
    for pattern in rule.reference_globs:
        abs_pat = os.path.join(repo_root, pattern)
        for hit in glob.glob(abs_pat, recursive=True):
            rel = os.path.relpath(hit, repo_root)
            if rel not in found:
                found.append(rel)
    return _rank_references(found, goal)[:_MAX_REFERENCES]


def _dedup(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def build_roadmap(goal: str, repo_root: str | None = None,
                  *, with_lessons: bool = False) -> Roadmap:
    """Assemble the roadmap for a goal.

    The core (skills, references, tokens, gates) is deterministic: no
    network, no model — same input always yields same output. The lessons
    leg is opt-in (`with_lessons=True`, set by the CLI) and best-effort, so
    the offline core and its tests stay pure while real invocations also
    surface past lessons to fold into the checklist.
    """
    repo_root = repo_root or os.getcwd()
    rules = detect_areas(goal)

    roadmap = Roadmap(goal=goal, areas=[r.name for r in rules])
    for rule in rules:
        roadmap.skills.extend(rule.skills)
        roadmap.tokens.extend(rule.tokens)
        roadmap.gates.extend(rule.gates)
        roadmap.references.extend(resolve_references(rule, goal, repo_root))

    roadmap.skills = _dedup(roadmap.skills)
    roadmap.tokens = _dedup(roadmap.tokens)
    roadmap.references = _dedup(roadmap.references)
    roadmap.gates = _dedup(roadmap.gates + list(BASE_GATES))
    if with_lessons:
        roadmap.lessons = recall_lessons(goal, roadmap.areas)
    return roadmap


def _bullets(items: list[str], empty: str) -> str:
    if not items:
        return f"  _{empty}_\n"
    return "".join(f"  - {it}\n" for it in items)


def _lesson_bullets(lessons: list[dict]) -> str:
    if not lessons:
        return "  _none recalled (memory off, or no past lesson matched)_\n"
    out = []
    for les in lessons:
        head = les.get("title") or les.get("snippet") or ""
        out.append(f"  - [{les.get('id')}] {head}\n")
    return "".join(out)


def render_markdown(roadmap: Roadmap) -> str:
    """Render the roadmap as the markdown scaffold the agent fills in.

    The Acceptance checklist is deliberately left as a prompt for the
    agent: deriving concrete, falsifiable criteria from a fuzzy goal is
    the one generative step, and it belongs to the consuming skill, not
    this deterministic router.
    """
    areas = ", ".join(roadmap.areas) or "none detected — treat as general"
    out = [
        f"# Roadmap — {roadmap.goal}\n",
        f"\n_Areas: {areas}_\n",
        "\n## Standards it MUST follow (read before building)\n",
        _bullets([f"skill: {s}" for s in roadmap.skills],
                 "no area matched; ask which conventions apply"),
        "\n## Lessons recalled from past sessions (fold the relevant ones into your checklist)\n",
        _lesson_bullets(roadmap.lessons),
        "\n## Reference components (mirror these — do not invent new patterns)\n",
        _bullets(roadmap.references,
                 "none found by glob; pick the closest existing view/module by hand"),
        "\n## Design tokens (use only these — no ad-hoc colors/spacing)\n",
        _bullets(roadmap.tokens, "n/a for this area"),
        "\n## Hard gates (the loop may NOT exit until all pass)\n",
        _bullets(roadmap.gates, "none"),
        "\n## Acceptance checklist — DERIVE 3-8 falsifiable items, then get them approved\n",
        "  _Agent: turn the goal + standards above into concrete, checkable\n"
        "  behaviors (states, counts, edge cases at 0/1/N). Each must be\n"
        "  verifiable by an independent reviewer, not by self-assessment._\n",
        "  - [ ] …\n",
    ]
    return "".join(out)


def roadmap_warning(roadmap: Roadmap) -> str | None:
    """Return an actionable warning if the roadmap is *hollow*, else None.

    A roadmap is hollow when no area fired — either the goal was
    empty/whitespace, or it described behavior without naming an area or
    file the lexical router recognizes. Either way the caller is about to
    emit a roadmap carrying only the base-gate floor (no skills,
    references, or area gates), which looks valid but pins no real bar.
    The library only *describes* the problem; printing is the CLI's job
    so stdout stays clean (e.g. parseable `--json`).
    """
    if roadmap.areas:
        return None
    if not roadmap.goal.strip():
        return ("goal is empty — preflight produced a hollow roadmap with no "
                "standards or references. Pass a real goal string.")
    return ("goal matched no known area — the roadmap is hollow (only the "
            "base gates apply). Rephrase naming the area (ui/frontend/python/"
            "cli/trace/docs) or a file path (e.g. lib/foo.py, *.vue).")


def roadmap_to_dict(roadmap: Roadmap) -> dict:
    """JSON-serializable view, for `--json` and programmatic callers."""
    return {
        "goal": roadmap.goal,
        "areas": roadmap.areas,
        "skills": roadmap.skills,
        "references": roadmap.references,
        "tokens": roadmap.tokens,
        "gates": roadmap.gates,
        "lessons": roadmap.lessons,
    }
