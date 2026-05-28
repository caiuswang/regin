"""GritQL rules endpoints: browse, inspect, ingest triggers, edit, delete.

Routes register onto `rules_bp` from sibling modules; this file just
defines the Blueprint and side-effect-imports each route module so the
registration runs at app boot.

Symbols tests monkey-patch (load_rules_index, deploy_rules_index_skill,
audit, rule_engines, grit_rule_index) are re-exported here. Submodules
access load_rules_index/deploy_rules_index_skill via
`from web.blueprints import rules as _pkg` and `_pkg.X` so the patches
reach call sites. Path config is read live from the `settings` instance.
"""

from __future__ import annotations

import subprocess  # noqa: F401  re-exported so tests can monkey-patch subprocess.run

from flask import Blueprint

from lib import audit, rule_engines
from lib.orm import SessionLocal
from lib.rules import grit_rule_index
from lib.rules.grit_rule_index import load_rules_index
from lib.skills.skill_deployer import deploy_rules_index_skill


rules_bp = Blueprint('rules', __name__)


# Side-effect imports register routes onto rules_bp. Keep AFTER both the
# Blueprint and the re-exports are defined so submodules can find them.
from web.blueprints.rules import (  # noqa: E402,F401
    applicability,
    crud,
    listing,
    triggers,
)
