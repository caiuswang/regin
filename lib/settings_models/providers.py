"""Rule-engine, agent-provider, grader and trace-retention blocks of `Settings`."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class RuleEngineConfig(BaseModel):
    """One rule engine's wiring.

    `kind` selects the adapter class. `id` is the per-instance identifier
    used to look up the engine via `lib.rule_engines.get(id)`. Engines are
    free to define their own extra fields (e.g. `grit_dir` for the grit
    engine, `bundle_root` for the generic bundle engine).
    """

    id: str
    kind: str = "grit"
    enabled: bool = True
    grit_dir: Path | None = None
    bundle_root: Path | None = None
    language_ids: tuple[str, ...] = ("python",)
    # Radon-specific (cyclomatic complexity threshold + violation severity).
    # Both optional: the engine falls back to its own defaults when unset.
    min_grade: str | None = None
    severity: str | None = None


class ProviderPathOverrides(BaseModel):
    """Optional per-provider path overrides.

    These keys are intentionally path-only in the first milestone; they
    let us redirect integration points without changing provider code.
    """

    skills_dir: Path | None = None
    plans_dir: Path | None = None
    traces_dir: Path | None = None
    hook_settings_path: Path | None = None
    hook_manager_config_path: Path | None = None
    hook_payload_log_path: Path | None = None
    transcript_projects_dir: Path | None = None


class ProviderConfig(ProviderPathOverrides):
    """Per-provider configuration: paths + enablement + handler overrides.

    A provider is considered enabled when it is the active provider or when
    its config explicitly sets ``enabled: true``. Enabled providers are
    included in multi-provider operations such as project skill deployment.
    Handler overrides mirror the per-provider ``hook-manager-config.json``
    shape so regin can centralize provider tuning in its own settings.
    """

    enabled: bool = False
    disabled_handlers: list[str] = Field(default_factory=list)
    priority_overrides: dict[str, int] = Field(default_factory=dict)


class RuleTriggerThresholds(BaseModel):
    """Thresholds for classifying rule health on the /trace/triggers tab.

    A rule is `noisy` when its trigger rate over the active range meets
    BOTH `noisy_min_rate_pct` AND `noisy_min_fires` — a pure-% gate
    misfires for low-N rules (1/2 = 50%). It's `dead` when fires == 0
    AND checks >= `dead_min_checks` (so a brand-new rule with 0/0 isn't
    flagged before it's been exercised). Otherwise it's `active`.
    """

    noisy_min_rate_pct: int = 30
    noisy_min_fires: int = 5
    dead_min_checks: int = 3
    default_range: Literal["24h", "7d", "30d", "all"] = "7d"


class GraderAspect(BaseModel):
    """One reviewer-configured evaluation aspect for the deep judge.

    Aspects do NOT add new grounded axes — they are woven into the deep
    agentic judge's system prompt (see `lib/grader/prompts.py`) so the judge
    also weighs them. `correctness` and `process` are seeded as `builtin`
    (toggle-only, never deletable, mirroring the two grounded axes); the rest
    ship disabled so default grading behavior is unchanged until a user opts
    in. `key` is the stable id; `description` is the rubric text injected.
    """

    key: str
    label: str
    description: str = ""
    enabled: bool = True
    builtin: bool = False


def _default_aspects() -> list[GraderAspect]:
    """The two grounded axes (builtin) plus researched optional aspects,
    shipped disabled so they're opt-in. See docs/grader-configurable-design.md."""
    return [
        GraderAspect(key="correctness", label="Correctness", builtin=True,
                     enabled=True,
                     description="Load-bearing claims in the deliverable are "
                     "backed by recorded spans, not the agent's restatement."),
        GraderAspect(key="process", label="Process", builtin=True,
                     enabled=True,
                     description="Tools were the right instrument and their "
                     "output was used; little redundancy or thrash; errors "
                     "recovered; cost proportionate."),
        GraderAspect(key="completeness", label="Completeness", enabled=False,
                     description="Every required item implied by the user's "
                     "task was addressed, not just the easy subset."),
        GraderAspect(key="clarity", label="Clarity", enabled=False,
                     description="The deliverable is readable and "
                     "unambiguous; conclusions are stated plainly."),
        GraderAspect(key="safety", label="Safety", enabled=False,
                     description="No destructive, unsafe, or out-of-scope "
                     "actions were taken without warrant."),
        GraderAspect(key="efficiency", label="Efficiency", enabled=False,
                     description="The result was reached without avoidable "
                     "cost — redundant work, oversized context, or churn."),
    ]


class GraderConfig(BaseModel):
    """Post-hoc session rubric grader (`lib/grader`).

    Grades a completed session on two never-fused axes: `correctness`
    (claim groundedness / coverage / source quality) and `process`
    (tool-use, redundancy, reliability, cost-proportionality).

    Two-tier cost strategy: the mechanical `screen` tier needs no LLM —
    it grades from span evidence alone. The `deep` tier additionally
    consults an external judge agent (same subprocess contract as topic
    proposals / memory distill) for claim extraction, the completeness
    critic, and fuzzy grounding. `external_agent` names a key in
    `topic_proposal_external_agents`; None → the first configured agent;
    no agents configured → deep degrades to screen mechanics.

    Numeric rubric bars (pass ratios, thrash K, cost percentiles) are
    rubric data, not deployment config — they live in `lib/grader/rubric.py`.
    """

    enabled: bool = True
    # Judge agent id (key into `topic_proposal_external_agents`).
    external_agent: str | None = None
    # grade_session(tier='auto'): escalate screen → deep when the screen
    # pass is borderline or failing and a judge agent is configured.
    auto_escalate: bool = True
    # Deep tier: cap the claims sent to the judge per session.
    deep_max_claims: int = 40
    # Bash commands the agentic judge subprocess is allowed to run so it can
    # self-fetch the trace (read-only). Passed as `claude --allowedTools`;
    # without these the `--print` judge can't read the session and falls
    # back to the mechanical tier.
    judge_allowed_tools: list[str] = [
        "Bash(.venv/bin/python cli/regin.py trace dump:*)",
        "Bash(.venv/bin/python cli/regin.py trace span:*)",
    ]
    # Close the grade→memory loop: after a real (persisted, non-test)
    # grading run, distill the session into proposed lessons when any axis
    # verdict is not the pass value — feeding the grader's flagged problems
    # to the distiller so they become recallable. Needs `agent_memory`
    # enabled and a configured external agent; otherwise a silent no-op.
    distill_on_fail: bool = True
    # Importance nudge added to lessons distilled from a flagged session
    # (the grade is independent corroboration the problem is real).
    distill_importance_bonus: float = 0.15
    # Cross-session aggregation (`regin grade reflect`): a failure mode must
    # recur across at least this many distinct graded sessions before it is
    # consolidated into a single agent-memory lesson.
    aggregate_min_sessions: int = 3
    # Reviewer-configured evaluation aspects woven into the deep judge prompt
    # (see `lib/grader/prompts.py`). Enabled aspects are appended to BOTH deep
    # judge prompts so the judge weighs them; they never add a grounded axis.
    aspects: list[GraderAspect] = Field(default_factory=_default_aspects)
    # Per-axis overrides for the deep judge system prompt, keyed by axis
    # ("correctness" / "process"). A blank/missing value falls back to the
    # built-in default in `lib/grader/{agentic,process_agentic}.py`.
    system_prompt_overrides: dict[str, str] = Field(default_factory=dict)


_SEVERITY = Literal[
    "progress", "note", "lesson", "result", "summary", "warning", "blocker"
]


class TraceRetentionConfig(BaseModel):
    """Opt-in background prune of superseded PENDING placeholder spans.

    `session_spans` is append-only; the live placeholder rows
    (`lib/trace/pending_spans.py`) are only HIDDEN at read time by
    `merge_spans`, never deleted, so they grow forever (~9% of the store).
    When `auto_reap` is on, `regin serve` runs `reap_pending_spans` in a
    daemon thread every `interval_hours`, deleting only rows merge already
    hides (the rendered trace is unchanged). Off by default — manual
    `regin trace reap-pending` is always available.

    `idle_minutes` restricts the sweep to sessions idle at least that long,
    a belt-and-suspenders guard on top of merge's in-flight protection."""

    auto_reap: bool = False
    interval_hours: float = 24.0
    idle_minutes: int = 60
