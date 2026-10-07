"""Feature #140 — arming a task with a launch provider.

`provider` rides inside the attempt's `params_json` (no schema change — the
column is Text), so a run launched through kimi keeps its provider next to its
other launch parameters. Claude-only dimensions (permission_mode/effort) are
validated against Claude's vocabulary only when the run is Claude's: a kimi
run skips them rather than dying on a vocabulary that means nothing there.
"""

from __future__ import annotations

import pytest

from lib.tasks.arm import parse_arm
from lib.tasks.capture import CaptureRejected


def test_arm_accepts_provider_kimi():
    fields = parse_arm({"provider": "kimi"})
    assert fields["params"]["provider"] == "kimi"


def test_arm_defaults_provider_claude():
    fields = parse_arm({})
    assert fields["params"]["provider"] == "claude"


def test_arm_rejects_unknown_provider():
    with pytest.raises(CaptureRejected) as err:
        parse_arm({"provider": "gpt5"})
    assert err.value.field == "provider"


def test_kimi_arm_skips_claude_only_params():
    """permission_mode/effort are Claude vocabulary; a kimi arm drops them
    instead of validating against words kimi has no use for."""
    fields = parse_arm({"provider": "kimi", "permission_mode": "acceptEdits",
                        "effort": "high"})
    params = fields["params"]
    assert params["provider"] == "kimi"
    assert params["permission_mode"] == ""
    assert params["effort"] == ""


def test_claude_arm_still_validates_its_vocabulary():
    with pytest.raises(CaptureRejected) as err:
        parse_arm({"provider": "claude", "permission_mode": "omniscient"})
    assert err.value.field == "permission_mode"
