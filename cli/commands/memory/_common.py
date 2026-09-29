"""The `memory` Typer group plus the output/scope helpers every
`regin memory` command shares."""

from __future__ import annotations

import os
from typing import Optional

import typer

from lib.activity_log import get_activity_logger


memory_app = typer.Typer(name="memory", help="Cross-session agent memory",
                         no_args_is_help=True)

_curate_log = get_activity_logger("memory")


def _print_memory_line(m: dict) -> None:
    title = m.get("title") or (m["body"][:60] + "…" if len(m["body"]) > 60
                               else m["body"])
    # Append compact suffix with importance and use_count (recall_count) if present
    suffix = ""
    importance = m.get("importance")
    recall_count = m.get("recall_count")
    if importance is not None or recall_count is not None:
        parts = []
        if importance is not None:
            parts.append(f"imp={importance:.2f}")
        if recall_count is not None:
            parts.append(f"use={recall_count}")
        if parts:
            suffix = " " + " ".join(parts)
    print(f"  {m['id'][:8]}  {m['tier']:8s} {m['status']:8s} "
          f"{m['kind']:10s} {m['scope']:14s} {title}{suffix}")


#: Session option shared by the walk commands. Optional everywhere: the walk
#: works without it, you just forgo the gate's proof that it ran.
_SESSION_OPT = typer.Option(
    None, "--session", "-s",
    help="Session/trace id to fingerprint this step under, so "
         "`regin gate recall-ran` can prove the walk happened. "
         "Get it from `regin session-id`.")


def _walk(session: Optional[str], tool: str, rendered: str, **attrs) -> str:
    """Print a walk step and leave the trace fingerprint the MCP tools leave.

    Without the fingerprint the walk is invisible to `regin gate recall-ran`,
    and a harness that can only reach the tree through this CLI would fail the
    anti-skip gate for having done the work the honest way. Two rules keep the
    fingerprint honest: only a render that surfaced something counts —
    otherwise `regin memory read <typo> --session $SID` is a one-command bypass
    of the gate that step 2 and step 4 both treat as a wall.
    """
    print(rendered)
    if not tree_nav_module().is_result(rendered):
        # Same convention as `recall`: nothing found is a non-zero exit, so
        # `regin memory index-root … || handle_it` works in a shell loop.
        raise typer.Exit(1)
    _fingerprint(session, tool, **attrs)
    return rendered


def _fingerprint(session: Optional[str], tool: str, **attrs) -> None:
    """Leave the gate's span for a step that really surfaced something.

    A span the ingest refused is reported on stderr. Silence there would leave
    the agent believing it had proof, then convict it of skipping — the
    unfollowable red gate that retired `ui-verified` (`lib/trace/span_gates`).
    """
    if not session:
        return
    from lib.hook_plugin import post_span
    landed = post_span(trace_id=session, name="memory.index.nav",
                       attributes={"tool": tool, **attrs})
    if not landed:
        typer.echo(
            f"warning: the {tool} span did not reach regin's ingest, so "
            f"`regin gate recall-ran --session {session}` will not see this "
            "walk. Start `regin serve` (or set REGIN_INGEST_BASE_URL) and "
            "re-run before treating a red gate as a skip.", err=True)


def _tool_scope(scope: Optional[str]) -> Optional[str]:
    """Apply `agent_memory.scope_policy` to a retrieval command's `--scope`.

    The CLI runs in the user's repo, so cwd is the signal the policy needs.
    An explicit `--scope` wins, and `--scope all` opts out entirely.
    """
    from lib.memory.scoping import resolve_tool_scope
    return resolve_tool_scope(scope, os.getcwd())


def tree_nav_module():
    """`lib.memory.tree_nav`, imported on use — the package __init__ pulls the
    store and embedding layers, which `regin memory --help` must not pay for."""
    from lib.memory import tree_nav
    return tree_nav


def _merged_graph(repo_path=None) -> dict:
    """Repo graph + global meta-roots — the topic id space `--topic` and the
    classifier validate against, so the meta-roots (`skills`, `preferences`, …)
    are fileable. Defaults to the project root; pass `repo_path` for `--repo`."""
    from pathlib import Path
    from lib.settings import settings
    from lib.topics.graph_io import load_authoritative_graph
    from lib.topics.meta_roots import merge_meta_roots
    root = repo_path if repo_path is not None else settings.project_root
    return merge_meta_roots(
        load_authoritative_graph(str(Path(root).resolve())))
