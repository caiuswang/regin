"""Seed the E2E scratch DB with the fixtures specs navigate to.

Run by `e2e-server.mjs` against an already-initialised scratch DB.

Rows are inserted directly rather than through `regin add-repo`, which appends
to `settings.repo_paths` and therefore *writes the developer's real
`config/settings.local.json`* — a config-level version of the database
pollution this whole setup removes.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lib.orm.engine import SessionLocal  # noqa: E402
from lib.orm.models.sync import Branch, Repo  # noqa: E402
from lib.trace import trace_service  # noqa: E402

# Several specs ("the first available session", the header/footer reload
# smoke tests) assert against whatever session happened to sit at the top of
# the list — on a developer machine, a real one. This is that session, made
# explicit: one prompt with a turn's worth of children under it.
BASELINE_TRACE = "e2e-baseline-session"

# Naive LOCAL time, seconds back — the format every real span writer stamps, and
# recent enough to fall inside the session list's default range. A fixed past
# date sorts the fixture off the list entirely.
#
# The baseline must also sort ABOVE the heavy fixture: several specs open "the
# first session in the list", and landing them on the heavy one points
# responsive/overflow assertions at deliberately pathological strings.
_BASE_TIME = datetime.now() - timedelta(seconds=30)


def _at(offset: int) -> str:
    return (_BASE_TIME + timedelta(seconds=offset)).isoformat(timespec="seconds")


def _baseline_spans() -> list[tuple[dict, dict]]:
    prompt = f"{BASELINE_TRACE}-prompt"
    rows = [
        ("prompt", prompt, None, {"text": "E2E baseline session fixture."}),
        ("assistant.thinking", f"{BASELINE_TRACE}-think", prompt, {}),
        ("tool.Read", f"{BASELINE_TRACE}-read", prompt,
         {"tool_name": "Read", "file_path": "/repo/example.py"}),
        ("tool.Bash", f"{BASELINE_TRACE}-bash", prompt,
         {"tool_name": "Bash", "command_preview": "pytest -q"}),
        ("assistant.response", f"{BASELINE_TRACE}-resp", prompt,
         {"text": "Done."}),
    ]
    out = []
    for i, (name, span_id, parent, attrs) in enumerate(rows):
        # Deliberately NOT marked `is_test`: the session list defaults to
        # `include_tests=False`, so a marked fixture is invisible to exactly the
        # specs that open "the first available session". The marker is a display
        # filter, not an isolation mechanism — isolation is the scratch DB.
        out.append(({
            "trace_id": BASELINE_TRACE, "span_id": span_id,
            "parent_id": parent, "name": name, "start_time": _at(i),
        }, attrs))
    return out


# The live card's "worst-case fixture": more turns than the 5-turn initial
# window (so the fold row renders), long unbroken strings (so the wrap rule is
# exercised), and system spans (so the default signal filter has something to
# suppress). This too was a UUID pointing at one real session.
HEAVY_TRACE = "e2e-heavy-session"
HEAVY_TURNS = 19
_LONG_CMD = "npx vite build --mode production --outDir " + "../web/static/dist/" * 8
_LONG_PATH = "/repo/" + "deeply/nested/".join([""] * 9) + "module_with_a_very_long_name.ts"


def _heavy_spans() -> list[tuple[dict, dict]]:
    # Anchored so the LAST span lands ~now: the live card reports `idle` for a
    # session whose tail is minutes old, and the NOW-zone specs assert an
    # active state.
    total = HEAVY_TURNS * 6 + 2
    # Deliberately older than the baseline session — see `_BASE_TIME`. Specs
    # that want this fixture navigate to it by id.
    tail = datetime.now() - timedelta(minutes=30)

    def heavy_at(i: int) -> str:
        return (tail - timedelta(seconds=total - i)).isoformat(timespec="seconds")

    out = []
    step = 0
    for turn in range(HEAVY_TURNS):
        prompt = f"{HEAVY_TRACE}-p{turn}"
        rows = [
            ("prompt", prompt, None, {"text": f"Heavy fixture step {turn}."}),
            ("tool.Bash", f"{HEAVY_TRACE}-bash{turn}", prompt,
             {"tool_name": "Bash", "command_preview": _LONG_CMD}),
            ("tool.Read", f"{HEAVY_TRACE}-read{turn}", prompt,
             {"tool_name": "Read", "file_path": _LONG_PATH}),
            # System spans: the card must render none of this vocabulary.
            ("turn", f"{HEAVY_TRACE}-turn{turn}", prompt, {}),
            ("hook.stop_summary", f"{HEAVY_TRACE}-hook{turn}", prompt, {}),
            ("cwd.changed", f"{HEAVY_TRACE}-cwd{turn}", prompt, {"cwd": "/repo"}),
        ]
        for name, span_id, parent, attrs in rows:
            out.append(({
                "trace_id": HEAVY_TRACE, "span_id": span_id,
                "parent_id": parent, "name": name, "start_time": heavy_at(step),
            }, attrs))
            step += 1
    # The NOW zone reads the last span; ending on a system span leaves it in no
    # documented state.
    out.append(({
        "trace_id": HEAVY_TRACE, "span_id": f"{HEAVY_TRACE}-final",
        "parent_id": f"{HEAVY_TRACE}-p{HEAVY_TURNS - 1}",
        "name": "assistant.response", "start_time": heavy_at(step),
    }, {"text": "Heavy fixture complete."}))
    # An explicit end makes the NOW zone's state a property of the DATA rather
    # than of how long the suite has been running: without it the zone reports
    # `idle` once the fixture's tail ages out, so the spec passed alone and
    # failed in a full run.
    out.append(({
        "trace_id": HEAVY_TRACE, "span_id": f"{HEAVY_TRACE}-end",
        "parent_id": None, "name": "session.end", "start_time": heavy_at(step + 1),
    }, {"reason": "clear"}))
    return out


def main() -> None:
    ingested, _ = trace_service.ingest_session_spans(_baseline_spans())
    print(f"seeded {ingested} baseline spans on {BASELINE_TRACE}")

    ingested, _ = trace_service.ingest_session_spans(_heavy_spans())
    print(f"seeded {ingested} heavy spans on {HEAVY_TRACE}")

    with SessionLocal() as session:
        repo = Repo(
            name=ROOT.name, path=str(ROOT), is_active=1,
            default_branch="master",
            description="E2E fixture repo (this checkout).",
        )
        session.add(repo)
        session.commit()
        session.refresh(repo)
        session.add(Branch(repo_id=repo.id, name="master", is_tracked=1))
        session.commit()
        print(f"seeded repo {repo.name} (id={repo.id})")


if __name__ == "__main__":
    main()
