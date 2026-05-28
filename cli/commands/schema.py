"""`regin schema ...` — payload schema bootstrap, list, diff, validate.

Each known PostToolUse tool has a JSON Schema at
`lib/trace/payload_schemas/<ToolName>.schema.json`. These commands help
keep those schemas in sync with the live payload corpus.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import typer


schema_app = typer.Typer(
    name="schema", help="Manage PostToolUse payload schemas",
    no_args_is_help=True,
)


_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "lib" / "trace" / "payload_schemas"
_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "hook_manager" / "tests" / "fixtures"
_DEFAULT_AGENT = "claude"


def _agent_dir(agent: str) -> Path:
    return _SCHEMA_DIR / agent


def _list_schemas(agent: str) -> list[str]:
    base = _agent_dir(agent)
    if not base.is_dir():
        return []
    return [p.name[: -len(".schema.json")] for p in sorted(base.glob("*.schema.json"))]


def _fixture_payloads(tool: Optional[str] = None) -> list[dict]:
    rows: list[dict] = []
    if not _FIXTURES_DIR.is_dir():
        return rows
    glob = "PostToolUse-*.json" if tool is None else f"PostToolUse-{tool}.json"
    for p in sorted(_FIXTURES_DIR.glob(glob)):
        try:
            rows.append(json.loads(p.read_text()))
        except Exception:
            pass
    return rows


def _payload_log_entries(tool: Optional[str], limit: int) -> list[dict]:
    """Pull recent payloads from ~/.claude/hook-payloads.jsonl."""
    from lib.providers import get_active_provider
    path = Path(str(get_active_provider().hook_payload_log_path()))
    if not path.is_file():
        return []
    rows: list[dict] = []
    # Tail the last `limit` entries cheaply by reading all lines (file
    # is size-capped at 50 MB, so this is fine).
    with path.open() as f:
        for line in f:
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("hook_event") != "PostToolUse":
                continue
            payload = entry.get("payload") or {}
            if tool is not None and payload.get("tool_name") != tool:
                continue
            rows.append(payload)
    return rows[-limit:]


@schema_app.command("list", help="List payload schemas: repo baseline + user overlay")
def cmd_schema_list(
    agent: str = typer.Option(_DEFAULT_AGENT, "--agent", help="Agent provider id"),
) -> None:
    from hook_manager.handlers.post_tool_trace import _TOOL_BUILDERS  # noqa: PLC0415
    from lib.settings import settings  # noqa: PLC0415

    base = _agent_dir(agent)
    overlay_root = Path(settings.payload_schemas_overlay_dir) / agent
    baseline_set = set(_list_schemas(agent))
    overlay_set = {
        p.name[: -len(".schema.json")]
        for p in overlay_root.glob("*.schema.json")
    } if overlay_root.is_dir() else set()

    print(f"baseline: {base}")
    print(f"overlay:  {overlay_root}{'' if overlay_root.is_dir() else ' (none yet)'}")
    print()
    known_tools = sorted(_TOOL_BUILDERS.keys()) + ["_mcp_wildcard"]
    for tool in known_tools:
        b = "B" if tool in baseline_set else "-"
        o = "O" if tool in overlay_set else "-"
        print(f"  [{b}{o}] {tool}.schema.json")
    extras = (baseline_set | overlay_set) - set(known_tools)
    for tool in sorted(extras):
        b = "B" if tool in baseline_set else "-"
        o = "O" if tool in overlay_set else "-"
        print(f"  [{b}{o}] {tool}.schema.json  (extra)")
    print()
    print("Legend: B=baseline (repo), O=overlay (per-user). Ratifies land in O.")


def _gather_samples(tool: str, source: str, limit: int) -> list[dict]:
    samples: list[dict] = []
    if source in ("fixtures", "both"):
        samples.extend(_fixture_payloads(tool))
    if source in ("payloads", "both"):
        samples.extend(_payload_log_entries(tool, limit))
    return samples


def _write_bootstrap_schema(
    tool: str, out_path: Path, samples: list[dict], agent: str,
) -> None:
    schema = _proposed_schema_from_samples(samples)
    schema["title"] = f"{tool} PostToolUse payload"
    schema["additionalProperties"] = True
    if agent == "claude":
        from lib.trace.claude_version import current_claude_version
        version = current_claude_version()
        schema["x-claude-versions"] = [version] if version else []
    out_path.write_text(json.dumps(schema, indent=2) + "\n")


@schema_app.command(
    "bootstrap",
    help="Draft schemas from fixtures + hook-payloads.jsonl via genson",
)
def cmd_schema_bootstrap(
    tool: Optional[str] = typer.Option(None, "--tool", help="Single tool to bootstrap"),
    source: str = typer.Option(
        "both", "--source",
        help="fixtures | payloads | both",
    ),
    limit: int = typer.Option(200, "--limit", help="Max payloads to sample"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing schemas"),
    agent: str = typer.Option(_DEFAULT_AGENT, "--agent", help="Agent provider id"),
) -> None:
    from hook_manager.handlers.post_tool_trace import _TOOL_BUILDERS
    targets = [tool] if tool else sorted(_TOOL_BUILDERS.keys())
    base = _agent_dir(agent)
    base.mkdir(parents=True, exist_ok=True)

    for t in targets:
        out_path = base / f"{t}.schema.json"
        if out_path.exists() and not force:
            print(f"skip {t}: already exists (use --force to overwrite)")
            continue
        samples = _gather_samples(t, source, limit)
        if not samples:
            print(f"skip {t}: no samples found")
            continue
        _write_bootstrap_schema(t, out_path, samples, agent)
        rel = out_path.relative_to(_SCHEMA_DIR.parents[2])
        print(f"wrote {rel} from {len(samples)} sample(s)")


@schema_app.command("validate", help="Validate fixtures and recent payloads for one tool")
def cmd_schema_validate(
    tool: str = typer.Argument(..., help="Tool name, e.g. Bash"),
    include_payloads: bool = typer.Option(
        True, "--payloads/--no-payloads",
        help="Also validate recent entries from ~/.claude/hook-payloads.jsonl",
    ),
    limit: int = typer.Option(200, "--limit", help="Max payload-log entries to scan"),
    agent: str = typer.Option(_DEFAULT_AGENT, "--agent", help="Agent provider id"),
) -> None:
    from lib.trace.payload_validation import validate

    samples = _fixture_payloads(tool)
    findings_total = 0
    for s in samples:
        findings = validate(tool, s, agent=agent)
        findings_total += len(findings)
        for f in findings:
            print(f"  [fixture] {f.drift_kind} @ {f.field_path}: {f.actual_sample}")

    if include_payloads:
        for s in _payload_log_entries(tool, limit):
            findings = validate(tool, s, agent=agent)
            findings_total += len(findings)
            for f in findings:
                print(f"  [live] {f.drift_kind} @ {f.field_path}: {f.actual_sample}")

    if findings_total == 0:
        print(f"OK: {tool} 0 drift findings")
    else:
        print(f"FAIL: {tool} {findings_total} drift finding(s)")
        raise typer.Exit(1)


def _prop_set(schema: dict, path: str = "") -> set[str]:
    """All known property paths in a schema, including nested + array items."""
    out: set[str] = set()
    if not isinstance(schema, dict):
        return out
    for k, v in (schema.get("properties") or {}).items():
        full = f"{path}.{k}" if path else k
        out.add(full)
        out |= _prop_set(v, full)
    items = schema.get("items")
    if isinstance(items, dict):
        out |= _prop_set(items, f"{path}[]")
    return out


def _print_diff(label: str, items: list[str]) -> None:
    if not items:
        return
    print(f"  {label} {len(items)} field(s):")
    for p in items:
        print(f"    {p}")


def _proposed_schema_from_samples(samples: list[dict]):
    from genson import SchemaBuilder
    builder = SchemaBuilder()
    builder.add_schema({"type": "object", "properties": {}})
    for s in samples:
        builder.add_object(s)
    return builder.to_schema()


@schema_app.command("diff", help="Show known properties vs proposed (from recent payloads)")
def cmd_schema_diff(
    tool: str = typer.Argument(..., help="Tool name"),
    limit: int = typer.Option(200, "--limit", help="Max payload-log entries to scan"),
    agent: str = typer.Option(_DEFAULT_AGENT, "--agent", help="Agent provider id"),
) -> None:
    schema_path = _agent_dir(agent) / f"{tool}.schema.json"
    if not schema_path.exists():
        print(f"no committed schema for {tool}")
        raise typer.Exit(1)

    samples = _fixture_payloads(tool) + _payload_log_entries(tool, limit)
    if not samples:
        print(f"no samples found for {tool}")
        raise typer.Exit(1)

    cur = _prop_set(json.loads(schema_path.read_text()))
    prop = _prop_set(_proposed_schema_from_samples(samples))
    only_proposed = sorted(prop - cur)
    only_current = sorted(cur - prop)

    print(f"diff for {tool} ({len(samples)} sample(s)):")
    _print_diff("+ in payloads, not in schema:", only_proposed)
    _print_diff("- in schema, not in payloads:", only_current)
    if not only_proposed and not only_current:
        print("  (no drift)")


def register(app: typer.Typer) -> None:
    """Register the `schema` group on the root CLI app."""
    app.add_typer(schema_app)
