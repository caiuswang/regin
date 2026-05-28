"""Repo-local topic graph API endpoints.

Routes register onto `topics_bp` from sibling modules; this file just
defines the Blueprint and side-effect-imports each route module so the
registration runs at app boot.

`regenerate_proposal_run` is re-exported here so tests can monkey-patch
it at the package level; the `maintenance` submodule reads it via
`from web.blueprints import topics as _pkg` so the patch reaches the
call site.
"""

from __future__ import annotations

from flask import Blueprint

from lib.topics.proposals import regenerate_proposal_run


topics_bp = Blueprint("topics", __name__)


# Side-effect imports register routes onto topics_bp.
from web.blueprints.topics import (  # noqa: E402,F401
    apply,
    graph,
    maintenance,
    proposals,
    workspace,
)
