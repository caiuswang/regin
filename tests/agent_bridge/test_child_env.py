"""`child_env` — the one definition of "the bridge is off for a child".

Every process regin launches on its own behalf goes through it, so the value
it forces (and the fact that it forces rather than defaults) is pinned here
rather than re-derived at each spawn site.
"""

from __future__ import annotations

from lib.agent_bridge import child_env


def test_an_empty_call_is_the_overlay_alone():
    """Launch surfaces that merge over the parent environment themselves (the
    Agent SDK) must not be handed a full copy of this process's env."""
    assert child_env() == {"REGIN_BRIDGE": "0"}


def test_a_base_environment_survives_apart_from_the_flag():
    base = {"PATH": "/bin", "REGIN_LLM_SURFACE": "drafting"}

    assert child_env(base) == {"PATH": "/bin",
                               "REGIN_LLM_SURFACE": "drafting",
                               "REGIN_BRIDGE": "0"}


def test_an_inherited_optin_is_overwritten_not_preserved():
    assert child_env({"REGIN_BRIDGE": "1"})["REGIN_BRIDGE"] == "0"


def test_the_base_mapping_is_not_mutated():
    base = {"REGIN_BRIDGE": "1"}

    child_env(base)

    assert base == {"REGIN_BRIDGE": "1"}
