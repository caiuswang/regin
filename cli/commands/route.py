"""`regin route` — single agent-facing dense routing entry point (EXPERIMENTAL).

Currently delegates to `lib.patterns.pattern_router.route()`. The top-level surface
exists so future mixing in of topic-router results or other sources doesn't
require renaming the agent-facing command.
"""

from __future__ import annotations

import json
import sys
from typing import Optional

import typer


def cmd_route(
    query: str = typer.Argument(..., help="Natural-language task or keyword query"),
    top_k: int = typer.Option(5, "--top-k", help="Number of results to return"),
    no_rerank: bool = typer.Option(False, "--no-rerank", help="Skip the cross-encoder rerank stage"),
    kinds: Optional[str] = typer.Option(
        None, "--kinds",
        help="Comma-separated source_kind filter (pattern, wiki). Default: both.",
    ),
    repo: Optional[str] = typer.Option(
        None, "--repo",
        help="Narrow wiki results to one registered repo name. Patterns are global "
             "and pass through unchanged.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table"),
) -> None:
    from lib.patterns import pattern_router
    from lib.skills import skill_router

    kind_list = [k.strip() for k in kinds.split(",")] if kinds else None
    repo_filter = repo if (repo and not isinstance(repo, typer.models.OptionInfo)) else None

    try:
        results = pattern_router.route(
            query, top_k=top_k, rerank=not no_rerank,
            kinds=kind_list, repo=repo_filter,
        )
    except skill_router.DependencyError as exc:
        print(str(exc), file=sys.stderr)
        raise typer.Exit(2)
    if json_out:
        print(json.dumps(results, indent=2))
        return
    if not results:
        print("no patterns indexed — run `regin pattern embed` first", file=sys.stderr)
        raise typer.Exit(1)
    score_kind = results[0]["score_kind"]
    print(f"Top {len(results)} result(s) [{score_kind}]:")
    for r in results:
        suffix = f"  [{r['source_kind']}]"
        if r.get("repo_name"):
            suffix += f" {r['repo_name']}"
        print(f"  {r['score']:+.4f}  {r['slug']:48s}  {r['title']}{suffix}")
        header = r.get("header")
        if header:
            print(f"           {header}")


def register(app: typer.Typer) -> None:
    app.command(
        "route",
        help="EXPERIMENTAL. Route a query through the dense pattern index",
    )(cmd_route)
