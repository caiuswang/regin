"""Handler: PostToolUse → run applicable rule-engine rules on an edited file.

Runs every configured engine that claims the edited file's language.
Grit keeps its repo-local `.grit/rules.json` behavior; non-Grit engines
load rules directly from their configured source bundle.

Each candidate rule is then gated by its **skill's scope**: rules whose
`guide` (pattern slug) is deployed only to certain repos fire only when
the edited file lives inside one of those repos. Skills deployed
globally fire everywhere; skills with no deployment row don't fire at
all. Rules without an attached guide keep the pre-refactor behavior
(treated as global). See `lib.patterns.pattern_scope`.
"""

from __future__ import annotations

import os
import uuid

from lib import languages
from lib import rule_engines
from lib import repo_config
from lib.patterns import pattern_scope
from lib.rule_engines.repo_scope import repo_for_path
from lib.rule_engines.base import RuleEngine
from ..core import HookPayload, HookResponse


_FALLBACK_EXTENSIONS = {
    'vue': ('.vue',),
    'css': ('.css',),
    'javascript': ('.js', '.jsx', '.mjs', '.cjs'),
    'typescript': ('.ts', '.tsx'),
    'python': ('.py',),
    'ruby': ('.rb',),
    'go': ('.go',),
    'rust': ('.rs',),
    'shell': ('.sh', '.bash'),
    'json': ('.json',),
    'yaml': ('.yaml', '.yml'),
    'markdown': ('.md',),
    'html': ('.html', '.htm'),
}


def _extensions_for_language(language_id: str, repo_root: str | None) -> tuple[str, ...]:
    configured = repo_config.effective_language_extensions(repo_root).get(language_id)
    if configured:
        return tuple(configured)
    try:
        return languages.get(language_id).file_extensions
    except KeyError:
        return _FALLBACK_EXTENSIONS.get(language_id, ())


def _engines_for_file(file_path: str, repo_root: str | None) -> list[tuple[RuleEngine, str]]:
    """Return engines configured for this file, paired with the language_id
    whose extension list matched. A single engine may register multiple
    languages (`['vue', 'css']`); we record only the one that actually
    selected this file so the trace shows the right tag (`grit·vue` not
    `grit·{vue,css}`). `repo_root` lets a repo-local `.regin/config.json`
    extend language→extension routing for files inside that repo.
    """
    matched: list[tuple[RuleEngine, str]] = []
    for engine in rule_engines.all_engines():
        for language_id in getattr(engine, 'language_ids', ()):
            if any(file_path.endswith(ext) for ext in _extensions_for_language(language_id, repo_root)):
                matched.append((engine, language_id))
                break
    return matched


def _extract_file_path(payload: HookPayload) -> str | None:
    tr = payload.tool_response or {}
    ti = payload.tool_input or {}
    for candidate in (tr.get('filePath'), ti.get('file_path')):
        if candidate and isinstance(candidate, str):
            return candidate
    return None


def _collect_applicable_rules(
    configured_engines: list[tuple[RuleEngine, str]],
    file_path: str,
    content: str,
    skipped_by_scope: list[dict],
) -> tuple[list[tuple[RuleEngine, dict | object, str | None]], int, bool, str | None]:
    """Evaluate every configured engine's rule pool against this file.

    Scope-gates each candidate rule (appending rejects to
    `skipped_by_scope`) and returns the surviving `applicable` rules, the
    total rules considered, whether any engine had an evaluable pool, and
    the repo_root discovered from a bundle (None → caller falls back to the
    file's dirname).
    """
    applicable: list[tuple[RuleEngine, dict | object, str | None]] = []
    engine_rule_totals: dict[str, int] = {}
    repo_root: str | None = None
    any_engine_evaluable = False

    def _scope_gate(rule_id, guide: str | None) -> bool:
        if pattern_scope.pattern_allowed_for_file(guide, file_path):
            return True
        skipped_by_scope.append({
            'rule_id': rule_id, 'guide': guide, 'reason': 'skill_scope',
        })
        return False

    for engine, _matched_lang in configured_engines:
        bundle = engine.applicable_rules(file_path, content)
        if bundle.total_in_pool == 0:
            continue
        any_engine_evaluable = True
        engine_rule_totals[engine.id] = bundle.total_in_pool
        if bundle.repo_root and not repo_root:
            repo_root = bundle.repo_root
        for run_engine, rule, guide in bundle.items:
            rule_id = getattr(rule, 'id', None) or (
                rule.get('id') if isinstance(rule, dict) else None
            )
            if not _scope_gate(rule_id, guide):
                continue
            applicable.append((run_engine, rule, guide))

    total_rules = sum(engine_rule_totals.values())
    return applicable, total_rules, any_engine_evaluable, repo_root


def _run_applicable_rules(
    applicable: list[tuple[RuleEngine, dict | object, str | None]],
    file_path: str,
    effective_root: str,
    repo_label: str,
    session_id: str | None,
) -> tuple[list[dict], list[str]]:
    """Run each applicable rule, returning the trigger events (one per rule)
    and the agent-facing violation lines (one per rule that matched)."""
    violations: list[str] = []
    trigger_events: list[dict] = []
    for engine, rule, guide in applicable:
        v = engine.run(rule, file_path, effective_root)
        match_count = v.match_count if v is not None else 0
        # The engine's per-rule detail (e.g. "aggregate CC=180 (threshold 130)"
        # or the offending function names) is the actionable part — surface it
        # so the agent sees *what* tripped, not just *that* something did.
        detail = v.detail if v is not None else None
        rule_id = getattr(rule, 'id', None) or rule.get('id')
        severity = getattr(rule, 'severity', None) or rule.get('severity', 'warn')
        summary = getattr(rule, 'summary', None) or rule.get('summary', '')
        trigger_events.append({
            'rule_id': rule_id,
            'file_path': file_path,
            'repo': repo_label,
            'match_count': match_count,
            'severity': severity,
            'guide': guide,
            'summary': summary,
            'detail': detail,
            'source': 'post-edit-hook',
            'session_id': session_id,
        })
        if match_count > 0:
            line = f"- `{rule_id}` ({severity}): {summary}"
            if detail:
                line += f" — {detail}"
            if guide:
                line += f" — guide: `patterns/{guide}.md`"
            violations.append(line)
    return trigger_events, violations


def _build_response_body(
    violations: list[str], rel: str, applicable_count: int, total_rules: int,
) -> str:
    if violations:
        return (
            f'rule-check: {len(violations)} rule violation(s) in `{rel}` '
            f'(checked {applicable_count} applicable of {total_rules} total):\n'
            + '\n'.join(violations)
            + '\n\nFix these before claiming the edit is complete.'
        )
    return f'rule-check: OK — `{rel}` passes {applicable_count} applicable rule(s).'


def _emit_no_applicable(
    payload: HookPayload,
    file_path: str,
    effective_root: str,
    engine_tags: list[dict],
    total_rules: int,
    skipped_by_scope: list[dict],
) -> HookResponse:
    """Emit the rule.check span and agent response for the case where every
    engine had an evaluable pool but no rule survived (out-of-scope or none
    applicable)."""
    status = 'all_rules_out_of_scope' if skipped_by_scope else 'no_applicable_rules'
    _emit_rule_check_span(
        payload.session_id,
        file_path,
        effective_root,
        applicable_rules=[],
        engine_tags=engine_tags,
        total_rules=total_rules,
        status=status,
        raw=payload.raw,
        skipped_by_scope=skipped_by_scope,
    )
    return HookResponse(
        suppress_output=True,
        additional_context=f'rule-check: {os.path.basename(file_path)} — no applicable rules',
    )


def _applicable_rule_summaries(trigger_events: list[dict]) -> list[dict]:
    return [
        {
            'id': ev['rule_id'],
            'severity': ev['severity'],
            'summary': ev['summary'],
            'guide': ev['guide'],
            'match_count': ev['match_count'],
            'violated': ev['match_count'] > 0,
        }
        for ev in trigger_events
    ]


def _prepare_check(payload: HookPayload):
    """Run the guard prologue: validate the tool/file, resolve the repo, pick
    engines, and read the content. Returns a 4-tuple
    `(file_path, registered_repo, configured_engines, content)` when the
    check should proceed, else None."""
    if payload.tool_name not in ('Edit', 'Write', 'MultiEdit'):
        return None

    file_path = _extract_file_path(payload)
    if not file_path or not os.path.isfile(file_path):
        return None

    # Resolve the file's repo once: it gates engine selection (a repo-local
    # `.regin/config.json` may extend language routing) and tags triggers.
    registered_repo = repo_for_path(file_path)
    repo_root = registered_repo.path if registered_repo else None

    configured_engines = _engines_for_file(file_path, repo_root)
    if not configured_engines:
        return None

    try:
        with open(file_path) as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return None

    return file_path, registered_repo, configured_engines, content


def handle(payload: HookPayload) -> HookResponse | None:
    prepared = _prepare_check(payload)
    if prepared is None:
        return None
    file_path, registered_repo, configured_engines, content = prepared

    # Refresh the per-pattern deployment cache: a deployment toggled
    # between hook invocations should take effect on the very next
    # check, not after a process restart.
    pattern_scope.reset_cache()
    registered_repo_name = registered_repo.name if registered_repo else None

    skipped_by_scope: list[dict] = []
    applicable, total_rules, any_engine_evaluable, repo_root = _collect_applicable_rules(
        configured_engines, file_path, content, skipped_by_scope,
    )

    if not any_engine_evaluable:
        return None

    engine_tags = [
        {'engine': e.id, 'language': lang}
        for e, lang in configured_engines
    ]
    effective_root = repo_root or os.path.dirname(file_path)
    if not applicable:
        return _emit_no_applicable(
            payload, file_path, effective_root, engine_tags,
            total_rules, skipped_by_scope,
        )

    repo_label = registered_repo_name or os.path.basename(effective_root)
    trigger_events, violations = _run_applicable_rules(
        applicable, file_path, effective_root, repo_label, payload.session_id,
    )

    rel = os.path.relpath(file_path, effective_root)
    _emit_span_and_post_triggers(
        payload, file_path, effective_root, engine_tags,
        total_rules, skipped_by_scope, trigger_events, bool(violations),
    )

    body = _build_response_body(violations, rel, len(applicable), total_rules)
    return HookResponse(suppress_output=True, additional_context=body)


def _emit_span_and_post_triggers(
    payload: HookPayload,
    file_path: str,
    effective_root: str,
    engine_tags: list[dict],
    total_rules: int,
    skipped_by_scope: list[dict],
    trigger_events: list[dict],
    has_violation: bool,
) -> None:
    """Emit the rule.check span (stamping its id onto every trigger row) and
    ingest the trigger events. The span is emitted BEFORE ingest so its id can
    stamp each row — that's the deep-link target for the /trace/triggers
    drawer's 'recent events' list."""
    span_id = _emit_rule_check_span(
        payload.session_id,
        file_path,
        effective_root,
        applicable_rules=_applicable_rule_summaries(trigger_events),
        engine_tags=engine_tags,
        total_rules=total_rules,
        status='violation' if has_violation else 'ok',
        raw=payload.raw,
        skipped_by_scope=skipped_by_scope,
    )
    if span_id:
        for ev in trigger_events:
            ev['span_id'] = span_id

    from lib.hook_plugin import post_event
    post_event('rule_triggers', trigger_events)


def _emit_rule_check_span(
    trace_id: str | None,
    file_path: str,
    repo_root: str,
    *,
    applicable_rules: list[dict],
    engine_tags: list[dict],
    total_rules: int,
    status: str,
    raw: dict | None = None,
    skipped_by_scope: list[dict] | None = None,
) -> str | None:
    """Emit a rule.check span and return its id.

    The returned span_id is stamped onto every rule_triggers row from
    the same check so the /trace/triggers drawer can deep-link an event
    back to this exact span in the session trace.
    """
    if not trace_id:
        return None
    span_id = uuid.uuid4().hex[:16]
    skipped = skipped_by_scope or []
    attributes: dict = {
        'file_path': file_path,
        'relative_path': os.path.relpath(file_path, repo_root),
        'status': status,
        'applicable_rules': applicable_rules,
        'engine_tags': engine_tags,
        'applicable_rule_count': len(applicable_rules),
        'violating_rule_count': sum(1 for r in applicable_rules if r.get('violated')),
        'total_rules': total_rules,
        'skipped_rule_count': len(skipped),
        'skipped_rules': skipped,
    }
    # When the edit that triggered this rule check ran inside a subagent,
    # Claude Code tags the hook payload with the subagent's `agent_id`
    # (+ optional `agent_type`). Carry both onto the rule.check span so
    # the trace projection's third-pass graft re-parents it under the
    # matching `subagent.start`, mirroring what post_tool_trace does for
    # the tool.* span. Without this, rule.check spans surface as
    # first-class children of the owning prompt and visually float
    # outside the subagent's window.
    raw = raw or {}
    agent_id = raw.get('agent_id')
    if agent_id:
        attributes['agent_id'] = agent_id
        agent_type = raw.get('agent_type')
        if agent_type:
            attributes['agent_type'] = agent_type
    try:
        from lib.hook_plugin import post_span  # type: ignore
        post_span(
            trace_id=trace_id,
            span_id=span_id,
            name='rule.check',
            attributes=attributes,
        )
    except Exception:
        pass
    return span_id
