"""`regin rules ...` — rule index, deployment, and targeted execution."""

from __future__ import annotations

import glob
import os
from typing import Optional

import typer


rules_app = typer.Typer(
    name="rules", help="Manage rule engines and targeted checks",
    no_args_is_help=True,
)


@rules_app.command("check", help="Validate one configured rule engine")
def cmd_rules_check(
    engine: str = typer.Option(
        "grit", "--engine",
        help="Configured engine id to validate, defaults to grit for back-compat",
    ),
) -> None:
    if engine == "grit":
        from lib.rules import grit_rule_index
        missing = grit_rule_index.missing_metadata()
        if not missing:
            rules = grit_rule_index.parse_grit_rules()
            print(f"OK: {len(rules)} rules, all metadata present.")
            return
        print(f"FAIL: {len(missing)} pattern(s) missing @rule metadata:")
        for rel, pattern_name, fields in missing:
            print(f"  {rel}: pattern {pattern_name} missing {', '.join(fields)}")
        raise typer.Exit(1)

    from lib import rule_engines

    try:
        eng = rule_engines.get(engine)
    except KeyError:
        print(f"Engine not configured: {engine}")
        raise typer.Exit(1)

    rules = eng.parse_rules()
    if not rules:
        print(f"FAIL: no rules found for engine {engine}")
        raise typer.Exit(1)

    print(f"OK: {len(rules)} rules parsed for engine {engine}.")


@rules_app.command("index", help="Regenerate rules.json, RULES.md, and verification sections")
def cmd_rules_index() -> None:
    from lib.rules import grit_rule_index
    summary = grit_rule_index.regenerate(write_guides=True)
    print(f"Parsed {summary['rules']} rules from .grit/patterns/java/")
    print(f"Wrote {summary['rules_json']}")
    print(f"Wrote {summary['rules_md']}")
    print(f"Updated {summary['guides_updated']} pattern guide Verification section(s)")


@rules_app.command("deploy", help="Regenerate index and deploy grit-rules skill")
def cmd_rules_deploy() -> None:
    from lib.rules import grit_rule_index
    from lib.skills.skill_deployer import deploy_rules_index_skill
    summary = grit_rule_index.regenerate(write_guides=True)
    skill_path = deploy_rules_index_skill(summary['rules_md'])
    print(f"Indexed {summary['rules']} rules, deployed skill to {skill_path}")


@rules_app.command("list-disabled", help="Show rule ids currently disabled from enforcement")
def cmd_rules_list_disabled() -> None:
    from lib.rules import grit_rule_index
    ids = sorted(grit_rule_index.load_disabled_rule_ids())
    if not ids:
        print("No rules disabled.")
        return
    print(f"{len(ids)} disabled rule(s):")
    for rid in ids:
        print(f"  {rid}")


def _rules_set_disabled(disabled: bool, id: Optional[str], guide: Optional[str]) -> None:
    from lib.rules import grit_rule_index
    from lib.skills.skill_deployer import deploy_rules_index_skill

    target_ids: list[str] = []
    if id:
        target_ids.append(id)
    if guide:
        rules = grit_rule_index.rules_for_guide(guide)
        if not rules:
            print(f"No rules linked to guide '{guide}'.")
            raise typer.Exit(1)
        target_ids.extend(r['id'] for r in rules)
    if not target_ids:
        print("--id or --guide is required for disable/enable.")
        raise typer.Exit(2)
    new_set = grit_rule_index.set_rules_disabled(target_ids, disabled)
    grit_rule_index.regenerate(write_guides=False)
    verb = 'disabled' if disabled else 'enabled'
    print(f"{verb} {len(target_ids)} rule(s): {', '.join(target_ids)}")
    print(f"Current disabled set: {len(new_set)} rule(s)")
    deploy_rules_index_skill(grit_rule_index.RULES_MD_PATH)


@rules_app.command("disable", help="Disable rules by id or by guide")
def cmd_rules_disable(
    id: Optional[str] = typer.Option(None, "--id", help="Rule id to target"),
    guide: Optional[str] = typer.Option(
        None, "--guide",
        help="Procedure guide id; disables all rules linked to it",
    ),
) -> None:
    _rules_set_disabled(True, id, guide)


@rules_app.command("enable", help="Enable rules by id or by guide")
def cmd_rules_enable(
    id: Optional[str] = typer.Option(None, "--id", help="Rule id to target"),
    guide: Optional[str] = typer.Option(
        None, "--guide",
        help="Procedure guide id; enables all rules linked to it",
    ),
) -> None:
    _rules_set_disabled(False, id, guide)


@rules_app.command(
    "run",
    help="Run one rule against target files via the configured rule engine",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": False},
)
def cmd_rules_run(
    ctx: typer.Context,
    engine: str = typer.Option(..., "--engine", help="Configured engine id, e.g. grit or a bundle id"),
    rule_id: str = typer.Option(..., "--rule", help="Rule id to execute"),
    repo: str = typer.Option(..., "--repo", help="Repository root"),
    file: list[str] = typer.Option(..., "--file", help="Target file path, glob, or directory; repeatable"),
) -> None:
    from lib import rule_engines

    try:
        eng = rule_engines.get(engine)
    except KeyError:
        print(f"Engine not configured: {engine}")
        raise typer.Exit(1)
    repo_root = os.path.abspath(repo)
    rules = eng.parse_rules()
    rule = next((r for r in rules if r.id == rule_id), None)
    if rule is None:
        print(f"Rule not found: {rule_id}")
        raise typer.Exit(1)
    if not os.path.isdir(repo_root):
        print(f"Repo not found: {repo_root}")
        raise typer.Exit(1)

    file_args = list(file) + list(ctx.args)
    file_paths = _resolve_target_files(eng, rule, file_args)

    violations = []
    for target_file in file_paths:
        violation = eng.run(rule, target_file, repo_root)
        if violation is not None:
            violations.append(violation)

    if not violations:
        if len(file_paths) == 1:
            print(f"OK: {rule_id} reported no matches for {file_paths[0]}")
        else:
            print(f"OK: {rule_id} reported no matches across {len(file_paths)} file(s)")
        return

    if len(file_paths) == 1:
        violation = violations[0]
        print(f"VIOLATION: {rule_id} matched {violation.match_count} time(s) in {violation.file_path}")
        if violation.detail:
            print(violation.detail)
        raise typer.Exit(1)

    total_matches = sum(v.match_count for v in violations)
    print(
        f"VIOLATION: {rule_id} matched {total_matches} time(s) across "
        f"{len(violations)} of {len(file_paths)} file(s)"
    )
    for violation in violations[:10]:
        line = f"  {violation.file_path}: {violation.match_count} match(es)"
        if violation.detail:
            line += f" - {violation.detail}"
        print(line)
    if len(violations) > 10:
        print(f"  ... {len(violations) - 10} more file(s)")
    raise typer.Exit(1)


def _resolve_target_files(eng, rule, file_args: list[str]) -> list[str]:
    resolved: list[str] = []
    for file_arg in file_args:
        file_path = os.path.abspath(file_arg)
        resolved.extend(_resolve_one_target(eng, rule, file_arg, file_path))
    return sorted(set(resolved))


def _resolve_one_target(eng, rule, file_arg: str, file_path: str) -> list[str]:
    if os.path.isdir(file_path):
        extensions = _rule_extensions(eng, rule)
        if extensions:
            matched_files: list[str] = []
            for ext in extensions:
                pattern = os.path.join(file_path, '**', f'*{ext}')
                matched_files.extend(
                    os.path.abspath(path)
                    for path in glob.glob(pattern, recursive=True)
                    if os.path.isfile(path)
                )
        else:
            matched_files = [
                os.path.abspath(path)
                for path in glob.glob(os.path.join(file_path, '**', '*'), recursive=True)
                if os.path.isfile(path)
            ]
        if not matched_files:
            print(f"No matching files found under directory: {file_path}")
            raise typer.Exit(1)
        return matched_files

    matched_files = [
        os.path.abspath(path) for path in glob.glob(file_arg, recursive=True)
        if os.path.isfile(path)
    ]
    if matched_files:
        return matched_files
    if os.path.isfile(file_path):
        return [file_path]

    print(f"Target file not found: {file_path}")
    raise typer.Exit(1)


def _rule_extensions(eng, rule) -> tuple[str, ...]:
    getter = getattr(eng, 'language_extensions', None)
    if callable(getter):
        extensions = tuple(getter(rule))
        if extensions:
            return extensions
    return ()


@rules_app.command("list", help="List parsed rules for one configured engine")
def cmd_rules_list(
    engine: str = typer.Option(..., "--engine", help="Configured engine id, e.g. grit or a bundle id"),
) -> None:
    from lib import rule_engines

    try:
        eng = rule_engines.get(engine)
    except KeyError:
        print(f"Engine not configured: {engine}")
        raise typer.Exit(1)

    rules = eng.parse_rules()
    if not rules:
        print(f"No rules found for engine: {engine}")
        return

    print(f"{len(rules)} rule(s) for engine {engine}:")
    for rule in rules:
        checker = rule.metadata.get('checker')
        source_file = rule.source_file
        if checker:
            print(f"  {rule.id} [{rule.severity}] checker={checker} source={source_file}")
        else:
            print(f"  {rule.id} [{rule.severity}] source={source_file}")
