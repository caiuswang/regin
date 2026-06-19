"""Rubric-as-data for both grading axes.

The rubric is the product: every reliability and anti-gaming property of
the grader is a property of these structures, not of the orchestration
code. Bars are kept here (not in `lib/settings.py`) because they define
*what a passing session is*, which should version with the rubric, not
with a machine's deployment config.

`RUBRIC_VERSION` is stamped onto every persisted grade so historical
grades stay interpretable after the bars move.
"""

from __future__ import annotations


RUBRIC_VERSION = "v1"

# ── Correctness axis (groundedness / coverage / source-quality triad) ──
CORRECTNESS_RUBRIC: dict = {
    "axis": "correctness",
    "version": RUBRIC_VERSION,
    "judge": {"isolation": "fresh_context",
              "sees": ["artifact", "trace", "rubric"]},
    "criteria": {
        "groundedness": {
            "unit": "claim",
            "verdicts": ["GROUNDED", "UNGROUNDED", "CONTRADICTED", "STALE"],
            "evidence_required": True,
            # all claims GROUNDED; zero CONTRADICTED
            "pass_ratio": 1.0,
            # a CONTRADICTED load-bearing claim gates the axis to fail
            "gate": {"on": "CONTRADICTED", "result": "fail"},
            "grounding_map": {
                "state": {"span": ["Read", "Grep"], "check": "lines_present"},
                "result": {"span": ["Bash"], "check": "exit_code_and_stdout"},
                "external": {"span": ["WebFetch", "WebSearch"],
                             "check": "consulted_and_supports"},
                "diagnostic": {"span": ["Read", "Bash"],
                               "check": "cause_and_effect_both"},
            },
            "stale_rule": "later mutating span on cited target ⇒ STALE",
        },
        "coverage": {
            "unit": "required_item",
            "checklist_source": "derive_from_task",
            "verdicts": ["COVERED", "PARTIAL", "MISSING"],
            "pass_ratio": 0.9,
            # any MISSING required item gates to at most needs_revision
            "gate": {"on": "MISSING", "result": "needs_revision"},
            "depends_on": "groundedness",
        },
        "source_quality": {
            "unit": "source",
            "verdicts": ["AUTHORITATIVE", "PROXY", "UNVERIFIED"],
            "pass_ratio": 0.8,
            # a load-bearing claim backed only by a proxy caps the axis
            "gate": {"on": "PROXY", "result": "needs_revision"},
        },
    },
    "bias_mitigations": {
        "require_evidence_not_assertion": True,
        "never_accept_paraphrased_tool_output": True,
        # verbosity is penalized structurally: unsupported assertions in a
        # longer answer become UNGROUNDED claims, not extra credit
        "verbosity_penalty": "via_ungrounded_accounting",
    },
    "ignore": ["hedges", "plans", "pre_existing_issues", "style_nits",
               "agent_reasoning"],
}

# ── Process / efficiency axis ──
# Deliberately judge-free: every P1-P4 criterion is mechanically checkable
# from regin's span timeline + token split, which is the trajectory-aware
# posture without an LLM in the loop. (An LLM-assisted process pass is a
# possible future tier; the rubric describes what the code enforces.)
PROCESS_RUBRIC: dict = {
    "axis": "process_efficiency",
    "version": RUBRIC_VERSION,
    "conditioned_on": "correctness_verdict",
    "criteria": {
        "tool_use_appropriateness": {
            "unit": "span",
            "verdicts": ["APPROPRIATE", "SUBOPTIMAL", "WASTED"],
            # share of (suboptimal + wasted) spans above which the axis
            # cannot be 'efficient' / is outright 'wasteful'
            "max_waste_share": 0.25,
            "wasteful_waste_share": 0.5,
            "checks": {
                "suboptimal": "cheaper_tool_existed",
                "wasted": "output_unused_downstream",
            },
        },
        "redundancy": {
            "unit": "episode",
            "reports": ["redundant_reads", "thrash_episodes", "re_derivations"],
            # consecutive same-failure Bash spans with no intervening edit
            "thrash_consecutive_failures": 3,
            "max_redundant_episodes": 3,
        },
        "reliability": {
            "unit": "error_span",
            "reports": ["errored", "recovered", "ignored"],
            "gate": {"on": "ignored_error_feeding_claim",
                     "result": "cap_acceptable"},
        },
        "cost_proportionality": {
            "unit": "session",
            "verdicts": ["PROPORTIONATE", "ELEVATED", "RUNAWAY"],
            "reference": ["per_task_class_percentile",
                          "cost_per_covered_item", "explicit_budget"],
            "elevated_percentile": 0.90,
            "runaway_percentile": 0.99,
            # optional hard budget (USD): above it → ELEVATED, above 2x →
            # RUNAWAY, taking precedence over the percentile reference
            "cost_budget_usd": None,
            # context-bloat sub-check: flag when cache-read share of the
            # context tokens exceeds this (context replay dominating spend)
            "cache_read_share_flag": 0.85,
            "gate": {"on": "RUNAWAY", "result": "wasteful"},
        },
    },
    "aggregate": "pareto(correctness_pass, cost) per task_class",
    "ignore": ["unavoidable_io_latency", "one_off_recovered_errors",
               "exploratory_reads_that_informed_a_decision"],
}


def correctness_rubric() -> dict:
    return CORRECTNESS_RUBRIC


def process_rubric() -> dict:
    return PROCESS_RUBRIC


__all__ = ["RUBRIC_VERSION", "CORRECTNESS_RUBRIC", "PROCESS_RUBRIC",
           "correctness_rubric", "process_rubric"]
