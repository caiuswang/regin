"""Pattern listing, detail, create/edit/delete, and rule enable/disable endpoints.

Routes register onto `patterns_bp` from sibling modules; this file just
defines the Blueprint and side-effect-imports each route module so the
registration runs at app boot.

Selected symbols (`grit_rule_index`, `deploy_rules_index_skill`) are
re-exported here so tests can monkey-patch them at the package level.
Submodules access these via `from web.blueprints import patterns as _pkg`
so the patch reaches the call sites. Path config (patterns dir, project
root) is read live from the `settings` instance, not re-exported.
"""

from __future__ import annotations

from flask import Blueprint

from lib import rule_engines
from lib.rules import grit_rule_index
from lib.skills.skill_deployer import deploy_rules_index_skill


patterns_bp = Blueprint("patterns", __name__)


# Side-effect imports register routes onto patterns_bp. Keep AFTER both
# the Blueprint and the re-exports are defined so submodules can find
# everything they need.
from web.blueprints.patterns import (  # noqa: E402,F401
    editing,
    listing,
    rules_toggle,
)
