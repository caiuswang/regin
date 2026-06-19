"""Skill listing, detail, deployment (push/pull/undeploy) and regeneration."""

from __future__ import annotations

import functools
import os

from flask import Blueprint, jsonify, request
from sqlmodel import select

from lib.auth import get_current_user, require_editor
from lib.providers import (
    active_provider_skill_paths,
    build_provider,
    enabled_provider_ids,
    enabled_provider_skill_paths,
    get_active_provider,
    get_enabled_providers,
    is_provider_id,
)
from lib.rule_engines import get as get_rule_engine
from lib.orm import SessionLocal
from lib.orm.models import Repo
from lib.skills.skill_deployer import (
    deploy_python_complexity_skill,
    deploy_rules_index_skill,
    undeploy_skill,
)
from lib import audit
from lib.patterns import pattern_deployments
from lib.rules import grit_rule_index
from lib.skills import skill_registry, skill_sync


skills_bp = Blueprint('skills', __name__)


def require_known_skill(view):
    """404 early if the URL's `<skill_id>` isn't a known skill.

    Rebuilds the skill list on each request via `skill_registry.all_ids()`
    so patterns added after the server started are picked up without a
    restart. Must be placed **below** `@require_editor` (closer to the
    function) so auth runs first and an unauthed request on an unknown
    skill still returns 401 rather than leaking 404 as a membership oracle.
    """
    @functools.wraps(view)
    def wrapper(skill_id, *args, **kwargs):
        if skill_id not in skill_registry.all_ids():
            return jsonify({'error': 'not found'}), 404
        return view(skill_id, *args, **kwargs)
    return wrapper


# ── Listing + detail ───────────────────────────────────────────

@skills_bp.route('/api/skills')
def api_skills():
    rows = []
    for sid, stype, src, dep, state in skill_sync.list_states():
        entry = skill_registry.get(sid)
        if stype == 'pattern':
            href = f'/patterns/{entry["procedure_id"]}'
        else:
            href = f'/skills/{sid}'
        rows.append({
            'id': sid,
            'type': stype,
            'state': state,
            'source': src,
            'deployed': dep,
            'href': href,
        })
    by_type = {'auto': [], 'pattern': []}
    for r in rows:
        by_type[r['type']].append(r)
    drift_count = sum(1 for r in rows if r['state'] != skill_sync.STATE_IN_SYNC)
    payload = {
        'rows': rows,
        'by_type': by_type,
        'drift_count': drift_count,
        'total': len(rows),
        'provider': active_provider_skill_paths(),
        'enabled_providers': enabled_provider_skill_paths(),
    }
    if request.args.get('include_deployments') in ('1', 'true'):
        all_deps = pattern_deployments.list_deployments()
        by_skill = {}
        by_project = {}
        for d in all_deps:
            by_skill.setdefault(d['pattern_slug'], []).append(d)
            if d.get('scope') == 'project':
                pid = d.get('project_id')
                if pid not in by_project:
                    by_project[pid] = {
                        'project_id': d['project_id'],
                        'project_name': d.get('project_name'),
                        'project_path': d.get('project_path'),
                        'items': [],
                    }
                by_project[pid]['items'].append(d)
        payload['deployments'] = all_deps
        payload['deployments_by_skill'] = by_skill
        payload['deployments_by_project'] = sorted(
            by_project.values(), key=lambda x: (x['project_name'] or '')
        )
    return jsonify(payload)


@skills_bp.route('/api/skills/<skill_id>')
@require_known_skill
def api_skill_detail(skill_id):
    entry = skill_registry.get(skill_id)
    if entry['type'] == 'pattern':
        return jsonify({'redirect': f'/patterns/{entry["procedure_id"]}'})

    state = skill_sync.state(skill_id)
    source = skill_registry.source_path(skill_id)
    deployed = skill_registry.deployed_path(skill_id)

    body_md = None
    preview_path = None
    if entry['type'] == 'auto':
        # Auto-deployed skills (e.g. grit-rules) store the real body in content.md
        # and SKILL.md is just a shim pointing to it.
        content_md = os.path.join(deployed, 'content.md')
        preview_path = content_md if os.path.isfile(content_md) else os.path.join(deployed, 'SKILL.md')

    if preview_path and os.path.isfile(preview_path):
        with open(preview_path) as f:
            raw = f.read()
        if raw.startswith('---'):
            parts = raw.split('---', 2)
            body_md = parts[2] if len(parts) >= 3 else raw
        else:
            body_md = raw

    files = []

    return jsonify({
        'skill_id': skill_id,
        'entry': entry,
        'state': state,
        'source_rel': source,
        'deployed': deployed,
        'body_md': body_md,
        'files': files,
        'provider': active_provider_skill_paths(),
        'enabled_providers': enabled_provider_skill_paths(),
    })


# ── Deployment: pull / push / push-to-project ──────────────────

@skills_bp.route('/api/skills/<skill_id>/pull', methods=['POST'])
@require_editor
@require_known_skill
def api_skills_pull(skill_id):
    result = skill_sync.pull(skill_id)
    ok = not result.startswith(('refused:', 'skipped:'))
    return jsonify({'ok': ok, 'msg': result})


@skills_bp.route('/api/skills/<skill_id>/push', methods=['POST'])
@require_editor
@require_known_skill
def api_skills_push(skill_id):
    data = request.get_json(silent=True) or {}
    force = data.get('force', False)
    result = skill_sync.push(skill_id, force=force)
    if result.startswith('confirm-force:'):
        return jsonify({'ok': False, 'confirm_force': True, 'msg': result[len('confirm-force: '):]})
    ok = not result.startswith(('refused:', 'skipped:'))
    if ok:
        user = get_current_user()
        deployed = skill_registry.deployed_path(skill_id)
        pattern_deployments.record_deployment(
            skill_id, 'global', None, deployed,
            user['id'] if user else None,
            provider=get_active_provider().provider_id,
        )
        audit.log_action(
            user['id'] if user else None,
            user['username'] if user else 'anon',
            'deploy_pattern',
            f'pattern:{skill_id}',
            {'scope': 'global', 'path': deployed,
             'provider': get_active_provider().provider_id},
        )
    return jsonify({'ok': ok, 'msg': result})


def _push_skill_to_provider(skill_id, provider, repo, force):
    """Push one skill to one provider's project skills dir; return a result row."""
    pid = provider.provider_id
    target_dir = os.path.join(repo.path, *provider.project_skills_subpath())
    try:
        result = skill_sync.push(skill_id, force=force, target_dir=target_dir)
    except FileNotFoundError as exc:
        return {'provider': pid, 'ok': False, 'msg': f'cannot deploy: {exc}'}
    if result.startswith('confirm-force:'):
        return {'provider': pid, 'ok': False, 'confirm_force': True,
                'msg': result[len('confirm-force: '):]}
    ok = not result.startswith(('refused:', 'skipped:'))
    return {'provider': pid, 'ok': ok, 'msg': result, 'target_dir': target_dir}


def _record_project_deployment(skill_id, repo, provider_id, deployed_path, user,
                               action='deploy_pattern'):
    """Write the pattern_deployments row + audit entry for one project deploy."""
    uid = user['id'] if user else None
    pattern_deployments.record_deployment(
        skill_id, 'project', repo.id, deployed_path, uid, provider=provider_id,
    )
    audit.log_action(
        uid, user['username'] if user else 'anon',
        action, f'pattern:{skill_id}',
        {'scope': 'project', 'project_id': repo.id, 'project_name': repo.name,
         'path': deployed_path, 'provider': provider_id},
    )


def _drift_response(results):
    """JSON response if any provider reports drift needing force, else None."""
    needs_force = [r for r in results if r.get('confirm_force')]
    if not needs_force:
        return None
    drifted = ', '.join(r['provider'] for r in needs_force)
    return jsonify({
        'ok': False,
        'confirm_force': True,
        'msg': f"Drift detected on {drifted}. Force overwrite?",
        'per_provider': results,
    })


def _finalize_project_push(skill_id, repo, results):
    """Turn per-provider push results into a JSON response, recording a
    deployment row for each provider that succeeded."""
    drift = _drift_response(results)
    if drift is not None:
        return drift

    succeeded = [r for r in results if r['ok']]
    failed = [r for r in results if not r['ok']]

    user = get_current_user() if succeeded else None
    for r in succeeded:
        _record_project_deployment(
            skill_id, repo, r['provider'],
            os.path.join(r['target_dir'], skill_id), user,
        )

    if failed:
        problems = '; '.join(f"{r['provider']}: {r['msg']}" for r in failed)
        return jsonify({'ok': False, 'msg': problems, 'per_provider': results})

    return jsonify({
        'ok': True,
        'msg': f'Pushed {skill_id} to {len(succeeded)} provider(s) in {repo.name}',
        'per_provider': results,
    })


@skills_bp.route('/api/skills/<skill_id>/push-to-project', methods=['POST'])
@require_editor
@require_known_skill
def api_skills_push_to_project(skill_id):
    entry = skill_registry.get(skill_id)
    if entry['type'] == 'auto':
        return jsonify({'ok': False, 'msg': f'{skill_id} is an auto skill and cannot be pushed to a project'})

    data = request.get_json(silent=True) or {}
    project_id = data.get('project_id')
    force = data.get('force', False)
    if not project_id:
        return jsonify({'ok': False, 'msg': 'project_id is required'}), 400

    with SessionLocal() as session:
        repo = session.exec(
            select(Repo).where(Repo.id == project_id, Repo.is_active == 1)
        ).first()
    if repo is None:
        return jsonify({'ok': False, 'msg': f'project {project_id} not found or inactive'}), 404

    results = [_push_skill_to_provider(skill_id, p, repo, force)
               for p in _enabled_skill_providers()]
    return _finalize_project_push(skill_id, repo, results)


# ── Deployment listing + removal ───────────────────────────────

@skills_bp.route('/api/skills/<skill_id>/deployments', methods=['GET'])
@require_known_skill
def api_skills_deployments(skill_id):
    rows = pattern_deployments.list_deployments(pattern_slug=skill_id)
    for r in rows:
        r['tracked'] = True
    rows.extend(pattern_deployments.untracked_project_deployments(skill_id))
    return jsonify({'deployments': rows})


@skills_bp.route('/api/skills/<skill_id>/backfill-deployment', methods=['POST'])
@require_editor
@require_known_skill
def api_skills_backfill_deployment(skill_id):
    """Record a project deployment that already exists on disk.

    Unlike push-to-project this copies nothing — it only writes the missing
    `pattern_deployments` row for a skill dir that is physically present in
    the repo. Guards against recording a phantom deployment by verifying the
    directory exists first.
    """
    data = request.get_json(silent=True) or {}
    project_id = data.get('project_id')
    if project_id is None:
        return jsonify({'ok': False, 'msg': 'project_id required'}), 400

    with SessionLocal() as session:
        repo = session.get(Repo, project_id)
    if repo is None:
        return jsonify({'ok': False, 'msg': f'project {project_id} not found'}), 404

    # If caller specifies a provider, backfill only that one; otherwise scan
    # every enabled provider's project skills dir.
    providers, err = _providers_for_request(data.get('provider'))
    if err:
        return err

    user = get_current_user()
    recorded = []
    for provider in providers:
        pid = provider.provider_id
        target_dir = os.path.join(repo.path, *provider.project_skills_subpath())
        deployed_path_val = os.path.join(target_dir, skill_id)
        if not os.path.isdir(deployed_path_val):
            continue
        _record_project_deployment(
            skill_id, repo, pid, deployed_path_val, user,
            action='backfill_deployment',
        )
        recorded.append(pid)

    if not recorded:
        return jsonify({
            'ok': False,
            'msg': f'{skill_id} is not deployed under any enabled provider in {repo.name}',
        }), 400

    return jsonify({
        'ok': True,
        'msg': f'Recorded {skill_id} → {repo.name} ({", ".join(recorded)})',
    })


@skills_bp.route('/api/skills/<skill_id>/project-deployment/<int:project_id>', methods=['DELETE'])
@require_editor
@require_known_skill
def api_skills_remove_project_deployment(skill_id, project_id):
    providers, err = _providers_for_request(request.args.get('provider'))
    if err:
        return err

    with SessionLocal() as session:
        repo = session.get(Repo, project_id)
    if repo is None:
        return jsonify({'ok': False, 'msg': f'project {project_id} not found'}), 404

    fs_removed_any = False
    row_removed_any = False
    provider_ids = []
    for provider in providers:
        pid = provider.provider_id
        provider_ids.append(pid)
        target_dir = os.path.join(repo.path, *provider.project_skills_subpath())
        fs_removed = undeploy_skill(skill_id, target_dir=target_dir)
        row_removed = pattern_deployments.remove_deployment(
            skill_id, 'project', project_id, provider=pid,
        )
        fs_removed_any = fs_removed_any or fs_removed
        row_removed_any = row_removed_any or row_removed

    # Also strip the pattern's grit rules from the repo's `.grit/` (mirror of
    # the sync done on project push). Best-effort — never block the undeploy.
    try:
        from lib.rules import grit_rule_index
        grit_rule_index.remove_guide_rules_from_repo(skill_id, repo.path)
    except Exception:
        pass

    user = get_current_user()
    audit.log_action(
        user['id'] if user else None,
        user['username'] if user else 'anon',
        'undeploy_pattern',
        f'pattern:{skill_id}',
        {'scope': 'project', 'project_id': repo.id,
         'project_name': repo.name,
         'filesystem_removed': fs_removed_any, 'row_removed': row_removed_any,
         'providers': provider_ids},
    )
    return jsonify({
        'ok': True,
        'msg': f'removed {skill_id} from {repo.name}'
               f' (files: {"yes" if fs_removed_any else "no"}, row: {"yes" if row_removed_any else "no"})',
    })


# GET /api/repos lives in web/blueprints/repos.py. The skills push-to-project
# flow now reads from there (it returns an envelope with the same id/name/path
# fields, just nested under `repos`).


@skills_bp.route('/api/pattern-deployments', methods=['GET'])
def api_pattern_deployments_list():
    return jsonify({'deployments': pattern_deployments.list_deployments()})


def _enabled_skill_providers():
    """Enabled providers that actually support skills deployment."""
    return [p for p in get_enabled_providers() if p.capabilities.skills]


def _providers_for_request(requested_provider):
    """Resolve the provider(s) a skill op targets from request input.

    A blank value fans out over every enabled provider. A non-blank value is
    validated against the registry first so a malformed `?provider=` returns a
    400 instead of letting `build_provider` raise an uncaught ValueError (a
    500) — matching the guard the diagnostics/schema-drift/settings blueprints
    already apply. Returns `(providers, error)` where `error` is a ready
    `(json, status)` response tuple, or None when the resolution succeeded.
    """
    if not requested_provider:
        return _enabled_skill_providers(), None
    if not is_provider_id(requested_provider):
        return None, (
            jsonify({'ok': False, 'msg': f'unknown provider: {requested_provider}'}),
            400,
        )
    return [build_provider(requested_provider)], None


@skills_bp.route('/api/skills/<skill_id>/undeploy', methods=['POST'])
@require_editor
@require_known_skill
def api_skills_undeploy(skill_id):
    # Undeploy from every enabled provider's global skills dir. Disable linked
    # rules only on the first call to avoid redundant rule edits.
    msgs = []
    providers = _enabled_skill_providers()
    for idx, provider in enumerate(providers):
        target_dir = str(provider.global_skills_dir())
        try:
            result = skill_sync.undeploy(
                skill_id, target_dir=target_dir,
                provider_id=provider.provider_id,
                disable_linked_rules=(idx == 0),
            )
            msgs.append(f'{provider.provider_id}: {result}')
        except Exception as exc:
            msgs.append(f'{provider.provider_id}: {exc}')
    return jsonify({'ok': True, 'msg': '; '.join(msgs)})


# ── Auto-skill regeneration (grit-rules and friends) ───────

@skills_bp.route('/api/skills/<skill_id>/regenerate', methods=['POST'])
@require_known_skill
def api_skills_regenerate(skill_id):
    entry = skill_registry.get(skill_id)
    if entry['type'] != 'auto':
        return jsonify({'ok': False, 'msg': 'only auto skills can be regenerated'})
    if skill_id == 'grit-rules':
        summary = grit_rule_index.regenerate(write_guides=True)
        deploy_rules_index_skill(summary['rules_md'])
        return jsonify({'ok': True, 'msg': f"Regenerated: {summary['rules']} rules indexed and deployed"})
    if skill_id == 'python-complexity':
        path = deploy_python_complexity_skill()
        return jsonify({'ok': True, 'msg': f"Regenerated: python-complexity deployed to {path}"})
    return jsonify({'ok': False, 'msg': f'unknown auto skill: {skill_id}'})
