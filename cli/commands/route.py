"""`regin route` — single agent-facing dense routing entry point (EXPERIMENTAL).

Delegates to `lib.patterns.pattern_router.route_unified()`, which returns two
complementary sections: `guidance` (user-authored patterns + per-repo wikis —
the *procedures* for the task) and `memories` (cross-session agent memories —
*caveats and facts* to keep in mind, not procedures). They are kept separate on
purpose: a memory must never out-rank a procedure as if it were one. `--kinds`
selects which sources participate; the default returns both sections.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional

import typer


def _silence_model_load_progress() -> None:
    """Suppress the cross-encoder/embedding model's `Loading weights …%`
    progress bars on this agent-facing path.

    The bars are emitted on stderr by the model's own `trust_remote_code`
    loader (tqdm), so `--json` stdout is already clean — but a caller that
    merges streams (e.g. Claude Code's Bash tool captures stdout+stderr
    together) would otherwise see the bars interleaved ahead of the JSON and
    could fail to parse it. `setdefault` lets an explicit operator override
    win. Set before `lib.skills.skill_router` triggers the lazy model load."""
    os.environ.setdefault("TQDM_DISABLE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")


def cmd_route(
    query: str = typer.Argument(..., help="Natural-language task or keyword query"),
    top_k: int = typer.Option(5, "--top-k", help="Number of results to return"),
    no_rerank: bool = typer.Option(False, "--no-rerank", help="Skip the cross-encoder rerank stage"),
    kinds: Optional[str] = typer.Option(
        None, "--kinds",
        help="Comma-separated source_kind filter (pattern, wiki, memory). "
             "Default: all three.",
    ),
    repo: Optional[str] = typer.Option(
        None, "--repo",
        help="Narrow wiki results to one registered repo name. Patterns are global "
             "and pass through unchanged.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table"),
) -> None:
    _silence_model_load_progress()
    from lib.patterns import pattern_router
    from lib.skills import skill_router

    kind_list = [k.strip() for k in kinds.split(",")] if kinds else None
    repo_filter = repo if (repo and not isinstance(repo, typer.models.OptionInfo)) else None

    try:
        results = pattern_router.route_unified(
            query, top_k=top_k, rerank=not no_rerank,
            kinds=kind_list, repo=repo_filter,
        )
    except skill_router.DependencyError as exc:
        print(str(exc), file=sys.stderr)
        raise typer.Exit(2)
    if json_out:
        print(json.dumps(results, indent=2))
        return
    _render_sections(results)


def _render_sections(results: dict) -> None:
    """Print the two route sections as tables, or exit non-zero when both
    are empty."""
    guidance = results.get("guidance") or []
    memories = results.get("memories") or []
    if not guidance and not memories:
        print("no results — run `regin pattern embed` (and check memory is enabled)",
              file=sys.stderr)
        raise typer.Exit(1)
    _print_section("Procedural guidance (patterns & wikis)", guidance)
    _print_section("Relevant memories (caveats & facts — not procedures)", memories)


def _print_section(heading: str, rows: list) -> None:
    """Render one route section as a table. Memory rows have no file to open,
    so a body snippet stands in for the file path."""
    if not rows:
        return
    print(f"{heading} [{rows[0]['score_kind']}]:")
    for r in rows:
        suffix = f"  [{r['source_kind']}]"
        if r.get("repo_name"):
            suffix += f" {r['repo_name']}"
        print(f"  {r['score']:+.4f}  {r['slug']:48s}  {r['title']}{suffix}")
        if r.get("header"):
            print(f"           {r['header']}")
        if r["source_kind"] == "memory" and r.get("body"):
            snippet = " ".join(r["body"].split())
            print(f"           {snippet[:160]}")


def register(app: typer.Typer) -> None:
    app.command(
        "route",
        help="EXPERIMENTAL. Route a query through the dense pattern index",
    )(cmd_route)
