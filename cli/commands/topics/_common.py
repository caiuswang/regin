"""The `topics` Typer group plus repo-path resolution shared by every
`regin topics` command."""

from __future__ import annotations

from pathlib import Path

import typer

from lib.settings import settings


topics_app = typer.Typer(
    name="topics",
    help="Maintain repo-local curated topic graphs",
    no_args_is_help=True,
)


def _registered_repo_by_name(repo: str) -> Path | None:
    """Resolve a bare repo name (e.g. `regin`) against `settings.repo_paths`,
    returning the registered tree only if it carries a `.regin/` directory."""
    for candidate in settings.repo_paths or []:
        candidate_path = Path(candidate).expanduser().resolve()
        if candidate_path.name == repo and (candidate_path / ".regin").is_dir():
            return candidate_path
    return None


def _repo_path(repo: str | None) -> Path:
    """Resolve `--repo` to an absolute repo root, with a useful error when
    the argument doesn't look like a real repo. Accepts either a path
    (absolute or relative) or a registered repo name from
    `settings.repo_paths` — the common typo `--repo regin` would
    otherwise resolve relative to cwd and silently miss the right tree.
    """
    if not repo:
        return Path(settings.project_root).resolve()

    resolved = Path(repo).resolve()
    if (resolved / ".regin").is_dir():
        return resolved

    # Name lookup against settings.repo_paths takes precedence over a bare
    # existing directory so the common typo `--repo regin` (which would
    # resolve cwd-relative) still finds the registered tree.
    named = _registered_repo_by_name(repo)
    if named is not None:
        return named

    # Fall back to any existing directory: `bootstrap` creates `.regin/` (so it
    # must accept a tree that doesn't have one yet) and `import` must no-op
    # gracefully on an un-bootstrapped repo from the post-merge git hook. The
    # other subcommands surface a missing graph as their own error downstream.
    if resolved.is_dir():
        return resolved

    known = [str(Path(p).expanduser().resolve()) for p in (settings.repo_paths or [])]
    hint = (
        "\n  registered repos: " + ", ".join(known) if known
        else "\n  (no repo_paths registered — pass an absolute path)"
    )
    raise typer.BadParameter(
        f"--repo {repo!r} doesn't resolve to an existing repo directory"
        f" (tried {resolved}).{hint}"
    )
