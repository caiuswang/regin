"""Rule bundles a repo ships itself (`<repo>/.regin/rules/`).

Read endpoint lists what a repo declares plus its trust state; the write
endpoints are the UI's side of `regin rules trust` / `untrust`. Discovery is
free, execution is not: a bundle names a runner script, so it stays inert
until someone with editor rights approves its code — see
`lib/rule_engines/bundle_trust.py` for what the fingerprint covers.
"""

from __future__ import annotations

from flask import Blueprint, jsonify
from sqlmodel import select

from lib.auth import require_editor
from lib.orm import SessionLocal
from lib.orm.models import Repo
from lib.rule_engines import bundle_trust
from lib.rule_engines.manifest import discover_repo_bundles


repo_bundles_bp = Blueprint('repo_bundles', __name__)


def _repo_path(name: str) -> str | None:
    with SessionLocal() as session:
        repo = session.exec(select(Repo).where(Repo.name == name)).first()
    return repo.path if repo else None


def _bundle_payload(repo_name: str, repo_path: str, bundle_root, manifest) -> dict:
    fingerprint = bundle_trust.fingerprint(bundle_root, manifest)
    state = bundle_trust.describe(repo_path, manifest.id, fingerprint)
    return {
        'bundle_id': manifest.id,
        'engine_id': f'{repo_name}:{manifest.id}',
        'root': str(bundle_root),
        'languages': list(manifest.language_ids),
        'description': manifest.description,
        'trusted': state['trusted'],
        'code_changed': state['code_changed'],
        'fingerprint': fingerprint[:12],
    }


@repo_bundles_bp.route('/api/repos/<name>/bundles')
def api_repo_bundles(name):
    """Bundles this repo ships, each with its trust state."""
    path = _repo_path(name)
    if path is None:
        return jsonify({'error': 'not found'}), 404
    bundles = [
        _bundle_payload(name, path, root, manifest)
        for root, manifest in discover_repo_bundles(path)
    ]
    return jsonify({'repo': name, 'path': path, 'bundles': bundles})


def _set_trust(name: str, bundle_id: str, trusted: bool):
    path = _repo_path(name)
    if path is None:
        return jsonify({'error': 'not found'}), 404
    if not trusted:
        removed = bundle_trust.untrust(path, bundle_id)
        return jsonify({'ok': True, 'trusted': False, 'removed': removed})
    for root, manifest in discover_repo_bundles(path):
        if manifest.id != bundle_id:
            continue
        bundle_trust.trust(path, bundle_id, bundle_trust.fingerprint(root, manifest))
        return jsonify({'ok': True, 'trusted': True})
    return jsonify({'error': f'no bundle {bundle_id!r} under {name}/.regin/rules'}), 404


@repo_bundles_bp.route('/api/repos/<name>/bundles/<bundle_id>/trust', methods=['POST'])
@require_editor
def api_trust_bundle(name, bundle_id):
    """Approve this bundle's current code to run on edits inside the repo."""
    return _set_trust(name, bundle_id, True)


@repo_bundles_bp.route('/api/repos/<name>/bundles/<bundle_id>/trust', methods=['DELETE'])
@require_editor
def api_untrust_bundle(name, bundle_id):
    """Revoke execution trust; the bundle stays discovered and listed."""
    return _set_trust(name, bundle_id, False)
