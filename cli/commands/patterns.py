"""`regin pattern ...` — pattern promotion to regin-skillhub."""

from __future__ import annotations

import json
import sys
from typing import Optional

import typer
from typer.models import OptionInfo


pattern_app = typer.Typer(
    name="pattern", help="Manage patterns (promote to regin-skillhub)",
    no_args_is_help=True,
)


@pattern_app.command("promote", help="Promote a pattern to a regin-skillhub skill bundle")
def cmd_pattern_promote(
    slug: str = typer.Argument(..., help="Pattern slug (matches patterns/<slug>/)"),
    version: str = typer.Option("1.0.0", "--version", help="Semver version (default: 1.0.0)"),
    skillhub_url: Optional[str] = typer.Option(
        None, "--skillhub-url",
        help="regin-skillhub server URL (default: settings.local.json "
             "skillhub_url or http://127.0.0.1:8322)",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite if this version already registered"),
) -> None:
    from lib.patterns import pattern_promoter
    try:
        result = pattern_promoter.promote(
            slug, version=version, skillhub_url=skillhub_url, force=force,
        )
    except pattern_promoter.PromoteError as e:
        print(f'promote failed: {e}', file=sys.stderr)
        raise typer.Exit(1)
    print(f'uploaded {result["bundle_filename"]} → {result["url"]}')
    bundled = result.get('bundled') or {}
    rules = bundled.get('grit_rules') or []
    scripts = bundled.get('scripts') or []
    if rules or scripts:
        print(f'  bundled: {len(rules)} grit rule(s), {len(scripts)} script(s)')
    resp = result['response'] or {}
    skill = resp.get('skill') or {}
    if skill:
        print(f'regin-skillhub registered {skill.get("name")} '
              f'{skill.get("current_version")}')


def _print_grit_summary(result) -> None:
    """Print the grit rules merged on import (nothing if the bundle had none)."""
    if not result.grit_rules:
        return
    langs = f" ({', '.join(result.grit_languages)})" if result.grit_languages else ""
    print(f'merged {len(result.grit_rules)} grit rule(s){langs}: '
          f'{", ".join(result.grit_rules)}')
    if result.enabled_languages:
        print(f'enabled grit language(s): {", ".join(result.enabled_languages)}')
    print(f'  → rules activate after: regin skills push --id {result.slug}')


@pattern_app.command("import", help="Import a skillhub bundle as a regin pattern")
def cmd_pattern_import(
    bundle: str = typer.Argument(..., help="Path to a regin-skillhub .zip bundle"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing pattern with the same slug"),
    slug: Optional[str] = typer.Option(None, "--slug", help="Import under a different pattern slug"),
) -> None:
    from lib.patterns import pattern_importer
    target_slug = None if slug is None or isinstance(slug, OptionInfo) else slug
    overwrite = False if isinstance(force, OptionInfo) else force
    try:
        result = pattern_importer.import_zip(bundle, force=overwrite, target_slug=target_slug)
    except pattern_importer.ImportConflictError as e:
        print(f'import failed: {e}', file=sys.stderr)
        raise typer.Exit(2)
    except pattern_importer.ImportError_ as e:
        print(f'import failed: {e}', file=sys.stderr)
        raise typer.Exit(1)

    print(f'imported pattern {result.slug} -> {result.pattern_dir}')
    print(f'files: {result.file_count}')
    _print_grit_summary(result)
    print(f'deploy it with: regin skills push --id {result.slug}')


@pattern_app.command(
    "import-dir",
    help="Batch-import every <root>/<name>/SKILL.md directory as a regin pattern",
)
def cmd_pattern_import_dir(
    root_dir: str = typer.Argument(
        ..., help="Directory whose children are skill folders (e.g. ~/.claude/skills)",
    ),
    on_conflict: str = typer.Option(
        "skip", "--on-conflict",
        help="When the pattern slug already exists: skip | overwrite | rename",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="List what would be imported, change nothing",
    ),
) -> None:
    import os
    from lib.patterns import pattern_importer

    expanded = os.path.expanduser(root_dir)
    on_conflict = (on_conflict or "skip").strip().lower()

    status_glyph = {
        "imported": "  + ",
        "overwritten": "  ↻ ",
        "renamed": "  ~ ",
        "skipped": "  · ",
        "failed": "  ! ",
        "planned": "  · ",
    }

    def _print(entry):
        glyph = status_glyph.get(entry.status, "  ? ")
        suffix = ""
        if entry.file_count is not None:
            suffix = f"  ({entry.file_count} file(s))"
        if entry.error and entry.status not in {"imported", "overwritten", "renamed"}:
            suffix = f"  — {entry.error}"
        elif entry.grit_rules:
            suffix += f"  [+{len(entry.grit_rules)} grit rule(s)]"
        slug_or_name = entry.slug or entry.name
        print(f"{glyph}{slug_or_name}{suffix}")

    try:
        results = pattern_importer.batch_import_skill_directory(
            expanded, on_conflict=on_conflict,
            dry_run=dry_run, progress=_print,
        )
    except pattern_importer.ImportError_ as exc:
        print(str(exc), file=sys.stderr)
        raise typer.Exit(1)

    print()
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    summary_parts = [f"{k}={v}" for k, v in counts.items() if v]
    print("summary:", ", ".join(summary_parts) if summary_parts else "(no changes)")
    if counts.get("failed"):
        raise typer.Exit(1)


@pattern_app.command(
    "embed",
    help="EXPERIMENTAL. Embed pattern bodies into the SkillRouter dense index",
)
def cmd_pattern_embed(
    force: bool = typer.Option(False, "--force", help="Re-embed even if hash + model match"),
    model_id: Optional[str] = typer.Option(
        None, "--model", help="Override embedding model (default: SkillRouter-Embedding-0.6B)",
    ),
    skip_reranker_warmup: bool = typer.Option(
        False, "--skip-reranker-warmup",
        help="Don't pre-download the reranker (it will pull on first `route` call instead)",
    ),
) -> None:
    from lib.patterns import pattern_router
    from lib.skills import skill_router
    target_model = model_id or skill_router.EMBEDDING_MODEL_ID

    def log(msg: str) -> None:
        print(f"[embed] {msg}", flush=True)

    try:
        log(f"loading embedding model: {target_model} (first run downloads ~1.2 GB)")
        skill_router._load_embedding(target_model)
        log("encoding pattern bodies")
        counts = pattern_router.index_patterns(
            model_id=target_model, force=force, progress=log,
        )
        if not skip_reranker_warmup:
            log(f"loading reranker model: {skill_router.RERANKER_MODEL_ID} (first run downloads ~1.2 GB)")
            skill_router._load_reranker(skill_router.RERANKER_MODEL_ID)
    except skill_router.DependencyError as exc:
        print(str(exc), file=sys.stderr)
        raise typer.Exit(2)
    log(f"done — indexed={counts['indexed']} skipped={counts['skipped']} missing={counts['missing']}")


@pattern_app.command(
    "route",
    help="EXPERIMENTAL. Route a query through the SkillRouter dense index",
)
def cmd_pattern_route(
    query: str = typer.Argument(..., help="Natural-language task or keyword query"),
    top_k: int = typer.Option(5, "--top-k", help="Number of results to return"),
    retrieval_k: int = typer.Option(20, "--retrieval-k", help="Top-K before rerank"),
    no_rerank: bool = typer.Option(False, "--no-rerank", help="Skip the cross-encoder rerank stage"),
    kinds: Optional[str] = typer.Option(
        None, "--kinds",
        help="Comma-separated source_kind filter (pattern, wiki). Default: both.",
    ),
    repo: Optional[str] = typer.Option(
        None, "--repo",
        help="Narrow wiki results to one registered repo name.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table"),
) -> None:
    from lib.patterns import pattern_router
    from lib.skills import skill_router

    kind_list = [k.strip() for k in kinds.split(",")] if kinds else None
    repo_filter = repo if (repo and not isinstance(repo, OptionInfo)) else None

    try:
        results = pattern_router.route(
            query, top_k=top_k, retrieval_k=retrieval_k, rerank=not no_rerank,
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
    for r in results:
        suffix = f"  [{r['source_kind']}]"
        if r.get("repo_name"):
            suffix += f" {r['repo_name']}"
        print(f"{r['score']:+.4f}  {r['slug']:48s}  {r['title']}{suffix}")


@pattern_app.command(
    "enable-rules",
    help="Scaffold a self-describing rule bundle inside a pattern directory",
)
def cmd_pattern_enable_rules(
    slug: str = typer.Argument(..., help="Pattern slug (matches patterns/<slug>/)"),
) -> None:
    from pathlib import Path
    from lib.rule_engines.manifest import scaffold_bundle
    from lib.settings import settings

    bundle_root = Path(settings.patterns_dir) / slug
    try:
        created = scaffold_bundle(bundle_root, slug=slug)
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        raise typer.Exit(1)
    except ValueError as exc:
        print(f"scaffold failed: {exc}", file=sys.stderr)
        raise typer.Exit(2)
    print(f"scaffolded rule bundle at {bundle_root}")
    for path in created:
        print(f"  + {path.relative_to(bundle_root)}")
    print("\nNext: regin pattern rules-doctor", slug)


@pattern_app.command(
    "rules-doctor",
    help="Validate a pattern bundle's manifest, runner, and rules",
)
def cmd_pattern_rules_doctor(
    slug: str = typer.Argument(..., help="Pattern slug (matches patterns/<slug>/)"),
) -> None:
    from pathlib import Path
    from lib.rule_engines.manifest import validate_bundle
    from lib.settings import settings

    bundle_root = Path(settings.patterns_dir) / slug
    if not bundle_root.is_dir():
        print(f"no such bundle: {bundle_root}", file=sys.stderr)
        raise typer.Exit(2)
    manifest, diags = validate_bundle(bundle_root)
    print(f"bundle: {bundle_root}")
    if manifest is not None:
        print(f"  id: {manifest.id}")
        print(f"  language_ids: {', '.join(manifest.language_ids)}")
        print(f"  runner: {manifest.runner.kind} → {manifest.runner.entry}")
    print()
    level_map = {'ok': 'OK   ', 'warn': 'WARN ', 'error': 'ERROR'}
    has_warn = has_error = False
    for d in diags:
        print(f"  [{level_map[d.level]}] {d.message}")
        if d.level == 'warn':
            has_warn = True
        if d.level == 'error':
            has_error = True
    if has_error:
        raise typer.Exit(2)
    if has_warn:
        raise typer.Exit(1)

