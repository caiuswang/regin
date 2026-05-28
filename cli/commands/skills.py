"""`regin skills ...` — manage deployed provider skill entries."""

from __future__ import annotations

import os
from typing import Optional

import typer

from lib.providers import get_active_provider


_PROVIDER = get_active_provider()
_SKILLS_LABEL = str(_PROVIDER.global_skills_dir())

skills_app = typer.Typer(
    name="skills", help=f"Manage {_SKILLS_LABEL} entries",
    no_args_is_help=True,
)


def _skill_targets(skill_id: Optional[str]) -> list[str]:
    from lib.skills import skill_registry
    if skill_id:
        known = skill_registry.all_ids()
        if skill_id not in known:
            print(f"unknown skill id: {skill_id}")
            print(f"known ids: {', '.join(known)}")
            raise typer.Exit(2)
        return [skill_id]
    return skill_registry.all_ids()


def _ensure_skills_supported() -> None:
    if _PROVIDER.capabilities.skills:
        return
    print(f"skills are not supported for provider: {_PROVIDER.display_name}")
    raise typer.Exit(2)


@skills_app.command("list", help="Show every managed skill and its sync state")
def cmd_skills_list() -> None:
    _ensure_skills_supported()
    from lib.skills import skill_sync
    rows = list(skill_sync.list_states())
    id_w = max(len(r[0]) for r in rows)
    type_w = max(len(r[1]) for r in rows)
    state_w = max(len(r[4]) for r in rows)
    print(f"{'SKILL'.ljust(id_w)}  {'TYPE'.ljust(type_w)}  "
          f"{'STATE'.ljust(state_w)}  SOURCE")
    # Resolve the project root once; every row is displayed relative to it
    # so the table stays readable even when PATTERNS_DIR lives under
    # $HOME/.local/share.
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    for skill_id, t, src, _dep, state in rows:
        rel_src = os.path.relpath(src, project_root)
        print(f"{skill_id.ljust(id_w)}  {t.ljust(type_w)}  "
              f"{state.ljust(state_w)}  {rel_src}")


@skills_app.command("check", help="Report drift; exit non-zero if any")
def cmd_skills_check(
    id: Optional[str] = typer.Option(None, "--id", help="Check a single skill"),
) -> None:
    _ensure_skills_supported()
    from lib.skills import skill_sync
    drifted = []
    for skill_id, t, _src, _dep, state in skill_sync.list_states():
        if id and skill_id != id:
            continue
        marker = ' ' if state == skill_sync.STATE_IN_SYNC else '!'
        print(f"  {marker} {skill_id:30s}  {t:11s}  {state}")
        if state != skill_sync.STATE_IN_SYNC:
            drifted.append(skill_id)
    if drifted:
        print(f"\n{len(drifted)} skill(s) need attention: {', '.join(drifted)}")
        raise typer.Exit(1)


@skills_app.command("pull", help="Copy deployed skill into regin source")
def cmd_skills_pull(
    id: Optional[str] = typer.Option(None, "--id", help="Pull a single skill (default: all)"),
) -> None:
    _ensure_skills_supported()
    from lib.skills import skill_sync
    for skill_id in _skill_targets(id):
        print(f"  {skill_id}: {skill_sync.pull(skill_id)}")


@skills_app.command("push", help="Copy source to global provider skills dir")
def cmd_skills_push(
    id: Optional[str] = typer.Option(None, "--id", help="Push a single skill (default: all)"),
    force: bool = typer.Option(False, "--force", help="Overwrite deployed even when drifted"),
) -> None:
    _ensure_skills_supported()
    from lib.skills import skill_sync
    for skill_id in _skill_targets(id):
        print(f"  {skill_id}: {skill_sync.push(skill_id, force=force)}")


@skills_app.command("undeploy", help="Remove a deployed skill directory")
def cmd_skills_undeploy(
    id: str = typer.Option(..., "--id"),
) -> None:
    _ensure_skills_supported()
    from lib.skills import skill_sync
    print(f"  {id}: {skill_sync.undeploy(id)}")
