"""Goal preflight — the portable recall + hard-gates kernel for `/goal-verified`.

Turns a freeform goal into a small *roadmap*: the universal hard gates that
decide "done", plus (opt-in) the past lessons recalled for the goal. The bar
is pinned *before* the agent builds, so verification has something falsifiable
to check against instead of the agent grading its own homework.

What this module deliberately does **not** do (removed 2026-06): route the goal
to per-area convention skills / reference components / design tokens. That used
to live here as a hardcoded `AREA_RULES` table keyed on regin's own paths and
skills — which (a) never generalized to another repo and (b) merely restated the
file-keyed convention table in CLAUDE.local.md, which is 100% precise on its
own. So the convention skills now come from that table (read by area before
editing); reference components come from reading the real target files in the
refine step (or, in the tree-nav arm, from the topic leaf's source refs). The
one irreducibly generative step — turning the gates into concrete, falsifiable
acceptance items — belongs to the agent that consumes the roadmap, not here.

The gate floor mirrors what the loop always requires (tests stay green, an
independent fresh-context reviewer checked the diff), so the roadmap never
invents a standard. The lessons leg is opt-in and rides the shared `lib.memory`
store — the same one `goal feedback` writes back to.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# The universal hard-gate floor: what every goal-verified run must pass
# regardless of area — the "no-sayer" baseline. Per-area machine gates
# (pytest / radon / vite / playwright) are read from the convention table in
# CLAUDE.local.md by the consuming skill, not invented here.
BASE_GATES: tuple[str, ...] = (
    "existing test suite stays green",
    "an independent reviewer (fresh context) checked the diff — /code-review high",
)

# How many past lessons to surface in a roadmap — a short menu the agent
# folds the relevant ones into its checklist, not a dump.
_MAX_LESSONS = 5


@dataclass
class Roadmap:
    """The assembled, pre-build bar for a goal: the hard gates plus any
    recalled lessons. Area-routed skills/references/tokens used to live here
    too; they now come from the file-keyed convention table and the refine
    step (see module docstring)."""

    goal: str
    gates: list[str] = field(default_factory=list)
    # Recalled lessons. Each: {id, title, snippet}. Populated only when
    # build_roadmap(with_lessons=True); empty otherwise so the deterministic
    # core stays pure and offline.
    lessons: list[dict] = field(default_factory=list)


def recall_lessons(goal: str, *, limit: int = _MAX_LESSONS) -> list[dict]:
    """Best-effort recall of past lessons relevant to a goal.

    Reuses the existing memory store's `recall` (FTS-capable, no embedder
    required) rather than any new index. Returns compact dicts carrying the
    memory **id**, so the consuming skill can later report which lessons made
    it into the approved roadmap — that inclusion is the clean engagement
    signal `goal feedback` records.

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
        # inflate the very usefulness signal we measure.
        hits = memory.recall(goal, top_k=max(limit * 2, limit), mode="fts",
                             reinforce=False)
    except Exception:
        return []

    out: list[dict] = []
    for hit in hits[:limit]:
        mem = hit.memory
        body = (mem.get("body") or "").strip().replace("\n", " ")
        out.append({
            "id": mem.get("id"),
            "title": mem.get("title") or "",
            "snippet": body[:160],
        })
    return out


def record_offered(session_id: str | None, lessons: list[dict],
                   goal: str) -> int:
    """Persist that `lessons` were *offered* to `session_id`.

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


def build_roadmap(goal: str, *, with_lessons: bool = False) -> Roadmap:
    """Assemble the roadmap for a goal: the universal hard-gate floor, plus
    (opt-in) recalled lessons.

    The gate floor is deterministic and offline. The lessons leg is opt-in
    (`with_lessons=True`) and **off by default at the CLI** as of 2026-06: it
    runs `recall_lessons` in FTS mode (lexical BM25 on the goal text, pre-code),
    the weakest recall rung, which measured ~22% injection engagement. Lesson
    recall has moved to the structure-first `regin memory recall-for-task` path
    (pulls a subsystem subtree by importance, not text similarity);
    `with_lessons=True` is retained for A/B-ing the old flat leg against it.
    """
    roadmap = Roadmap(goal=goal, gates=list(BASE_GATES))
    if with_lessons:
        roadmap.lessons = recall_lessons(goal)
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

    Two sections only: the recalled lessons (a menu to fold in) and the hard
    gates (the floor). Per-area convention skills and reference components are
    not routed here — read the skills from the file-keyed convention table in
    CLAUDE.local.md, and surface references by reading the real target files.
    The Acceptance checklist is deliberately left as a prompt: deriving
    concrete, falsifiable criteria from a fuzzy goal is the one generative
    step, and it belongs to the consuming skill, not this kernel.
    """
    out = [
        f"# Roadmap — {roadmap.goal}\n",
        "\n## Lessons recalled from past sessions (fold the relevant ones into your checklist)\n",
        _lesson_bullets(roadmap.lessons),
        "\n## Hard gates (the loop may NOT exit until all pass)\n",
        _bullets(roadmap.gates, "none"),
        "\n## Acceptance checklist — DERIVE 3-8 falsifiable items, then get them approved\n",
        "  _Agent: turn the goal + the area's convention skills (the file-keyed\n"
        "  table in CLAUDE.local.md) into concrete, checkable behaviors (states,\n"
        "  counts, edge cases at 0/1/N). Each must be verifiable by an\n"
        "  independent reviewer, not by self-assessment._\n",
        "  - [ ] …\n",
    ]
    return "".join(out)


def roadmap_warning(roadmap: Roadmap) -> str | None:
    """Return a warning if the goal is empty/whitespace, else None.

    With area-routing removed there is nothing else for a roadmap to be
    *hollow* about: any real goal gets the universal gate floor and an
    (opt-in) lessons recall. An empty goal is the one degenerate case worth
    flagging. The library only *describes* the problem; printing is the CLI's
    job so stdout stays clean (e.g. parseable `--json`).
    """
    if roadmap.goal.strip():
        return None
    return ("goal is empty — pass a real goal string so the roadmap can recall "
            "lessons and pin the bar.")


def roadmap_to_dict(roadmap: Roadmap) -> dict:
    """JSON-serializable view, for `--json` and programmatic callers."""
    return {
        "goal": roadmap.goal,
        "gates": roadmap.gates,
        "lessons": roadmap.lessons,
    }
