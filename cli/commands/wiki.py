"""`regin wiki ...` — manage the wiki-page dense index.

Approved-topic wiki pages (per-topic markdown under
`<repo>/.regin/topics/wiki/`) are indexed into the same `pattern_docs`
trio as user-authored patterns, with `source_kind='wiki'`. Auto-reindex
runs on every successful accept; this CLI exists for cold-start
backfill (existing accepted wikis that predate the indexer) and for
manual force-refresh.
"""

from __future__ import annotations

import sys
from typing import Optional

import typer
from sqlmodel import select


wiki_app = typer.Typer(
    name="wiki",
    help="Manage the wiki-page dense index (per-topic accepted wikis)",
    no_args_is_help=True,
)


@wiki_app.command(
    "index",
    help="Embed approved-topic wiki pages into the dense index",
)
def cmd_wiki_index(
    repo: Optional[str] = typer.Option(None, "--repo", help="Index one repo by name"),
    all_: bool = typer.Option(False, "--all", help="Index every active registered repo"),
    force: bool = typer.Option(False, "--force", help="Re-embed even if hash + model match"),
) -> None:
    from lib.orm import SessionLocal
    from lib.orm.models import Repo
    from lib.patterns import wiki_indexer
    from lib.skills import skill_router

    target_one = repo and not isinstance(repo, typer.models.OptionInfo)
    if not target_one and not all_:
        all_ = True  # default: backfill every repo

    with SessionLocal() as s:
        if target_one:
            repos = s.exec(select(Repo).where(Repo.name == repo)).all()
            if not repos:
                print(f"no repo named {repo!r}", file=sys.stderr)
                raise typer.Exit(2)
        else:
            repos = s.exec(select(Repo).where(Repo.is_active == 1)).all()

    if not repos:
        print("no registered repos to index", file=sys.stderr)
        raise typer.Exit(1)

    def log(msg: str) -> None:
        print(f"[wiki index] {msg}", flush=True)

    totals = {"indexed": 0, "skipped": 0, "removed": 0, "missing": 0}
    try:
        for r in repos:
            log(f"repo={r.name}")
            counts = wiki_indexer.index_wikis(r, force=force, progress=log)
            for k in totals:
                totals[k] += counts.get(k, 0)
    except skill_router.DependencyError as exc:
        print(str(exc), file=sys.stderr)
        raise typer.Exit(2)

    log(
        f"done — indexed={totals['indexed']} skipped={totals['skipped']} "
        f"removed={totals['removed']} missing={totals['missing']}"
    )
