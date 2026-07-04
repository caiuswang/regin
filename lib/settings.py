"""Typed settings for regin (pydantic-settings).

Single declarative source for every path, port, and mode the app reads
at boot. Replaces the hand-rolled merge of `config/settings.json` +
`config/settings.local.json` + `REGIN_*` env vars that used to live in
`lib/config.py`.

Precedence (highest to lowest), mirroring the old behavior:

    REGIN_* environment variable
    > config/settings.local.json (machine-local, gitignored)
    > config/settings.json        (shared, git-tracked)
    > field default (derived from REGIN_DATA_DIR / XDG_DATA_HOME / ~)

The legacy module-level constants that used to live in `lib/config.py`
(PATTERNS_DIR, PROJECT_ROOT, …) plus the settings.json CRUD helpers
(save_settings, get_current_values, SETTINGS_SCHEMA) now live at the
bottom of this module. New code should prefer the `settings` instance.

Hot-path env vars consumed inside request handlers (`REGIN_INGEST_*`,
`REGIN_TRACE_TEST*`) are intentionally NOT surfaced here — those are
read at call time so tests can monkey-patch them. They will migrate
when their consuming modules are refactored.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal, Tuple, Type

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


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


class TopicProposalExternalAgent(BaseModel):
    """One external command that can draft topic proposals / judge sessions.

    The prompt is piped on stdin (the `claude --print` / `codex exec`
    convention). An agent whose CLI takes the prompt as an argument instead
    (Kimi's `-p <prompt>`) puts a literal ``{prompt}`` token in `args`; the
    runner substitutes the prompt there and writes no stdin.

    `supports_allowed_tools` gates the grader's `--allowedTools` grant: agents
    without that flag (Kimi) must auto-approve the read-only trace commands via
    their own permission config instead.
    """

    command: str
    args: list[str] = Field(default_factory=list)
    timeout_seconds: int = 600
    cwd: Path | None = None
    supports_allowed_tools: bool = True


class AgentMemoryConfig(BaseModel):
    """Cross-session agent memory (`lib/memory`).

    The memory engine lives in its **own** SQLite file so it survives
    `regin init` / `rebuild` and never enters the `db/schema.sql` vs
    Alembic drift trap — the engine initializes its own schema on first
    use. `db_path=None` resolves to `<project_root>/db/regin_memory.db`.

    `auto_inject` gates the UserPromptSubmit `<recalled_experience>`
    handler. That handler always recalls FTS-only (hooks are short-lived
    processes; loading the dense models per prompt is a non-starter) —
    `dense_enabled` only governs the long-lived surfaces (recall MCP
    tool, web API, CLI), and degrades to FTS when torch/transformers are
    missing, per the EmbeddingProvider port contract.

    `scope_policy` is the wrapper-level write scope: `global` stores
    everything in one scope; `per-repo` stamps captures from a registered
    repo's cwd as `repo:<name>` and narrows recall to it; the default
    `per-repo-tagged` stamps the repo on writes (so memories carry their
    repo category) while recall stays globally visible.

    `inject_min_overlap` gates the FTS-only auto-inject path: a memory
    must share at least this many distinct *informative* content tokens
    with the prompt to be injected. BM25 always ranks *something*, so
    without the gate a grown store attaches tangential memories to every
    prompt. 0 disables.

    `overlap_idf_max_df` makes that overlap idf-aware: a token appearing in
    more than this fraction of active memories is corpus-saturating
    ('session'/'memory'/'trace' in this repo) and does not count toward the
    overlap, so coincidental matches on common words never clear the gate.
    0 (or ≥1) disables idf filtering — the gate then counts raw tokens.

    `inject_fts_top_k` caps how many hits the auto-inject renders when the
    surfaced hits carry *no* calibrated rerank confidence (the dense server
    path was unavailable and recall fell to FTS/RRF rank order). Without a
    confidence score we trust only the single strongest lexical match, not
    a speculative top-k. Reranked surfaces still honour `inject_top_k`.
    """

    enabled: bool = True
    db_path: Path | None = None
    auto_inject: bool = True
    inject_top_k: int = 3
    inject_max_chars: int = 2_000
    # Skill experience: when a prompt invokes a skill via slash command
    # (`/playwright-screenshots …`), inject a `<skill_experience>` block of the
    # memories filed under that skill's meta-leaf (`skill-<command>`, see
    # lib/topics/meta_roots.py) — the skill analog of `<recalled_experience>`,
    # delivered on invocation even when the generic auto-inject is skipped for
    # that command. Off → no skill-scoped block. `_max_chars` budgets the block.
    skill_experience_inject: bool = True
    skill_experience_max_chars: int = 1_200
    inject_min_overlap: int = 3
    overlap_idf_max_df: float = 0.30
    inject_fts_top_k: int = 1
    # Slash commands whose own machinery already pulls context/recall, so the
    # UserPromptSubmit auto-inject (`<recalled_experience>` / `<topic_context>`)
    # is redundant noise on them. When the prompt's command token is in this
    # list, `_eligible_prompt` returns False and nothing is injected (and no
    # `memory.recall` span is emitted). Matched on the command token only — the
    # first whitespace-delimited word — case-insensitively, with the leading
    # slash optional per entry: "goal" and "/GOAL" both match `/goal`, and
    # `/goal` never prefix-matches `/goalpost`. Empty list → inject on every
    # eligible prompt (prior behaviour). Default skips `/goal` and
    # `/goal-verified`, which run their own preflight recall.
    inject_skip_commands: list[str] = Field(
        default_factory=lambda: ["/goal", "/goal-verified"])
    # Same-session dedup: skip injecting a memory already injected earlier
    # this session (tracked in the memory DB's `injection_events` table, so
    # it survives the per-prompt fresh hook process). When a previously
    # injected memory re-surfaces on a later prompt it is reinforced once —
    # repeated relevance across a session is a usefulness signal the
    # speculative auto-inject otherwise never gets. Off → re-inject freely.
    inject_dedup_session: bool = True
    # Dense recall at inject time. A hook is a short-lived process and can't
    # load the embedder per prompt, so when this is on the handler asks the
    # already-warm `regin serve` process instead (POST /api/memory/recall,
    # mode=auto → dense + rerank) over loopback, with a short timeout and a
    # clean fall back to in-process FTS when the server is down or slow.
    # The recall endpoint stays auth-gated to the network; the auth gate
    # grants a loopback-only exemption for it when this flag is on.
    inject_dense_via_server: bool = True
    inject_server_url: str = "http://127.0.0.1:8321"
    # Budget for the loopback dense-recall borrow. Warm latency of the full
    # embed + cross-encoder-rerank pull (20 candidates) is ~0.7s, but an
    # *idle-rewarm* call — the model went idle and the next request re-warms
    # it — was measured at ~2.6s. Auto-inject prompts arrive sporadically, so
    # they keep hitting that idle penalty: at the old 1.5s budget the rerank
    # lost the race and silently degraded to FTS (observed: every recall in a
    # real session fell to FTS despite a running, warmed server). 3.0s clears
    # the 2.6s idle spike with margin. A *down* server fails instantly
    # (connection refused, not a timeout wait), so this roomier budget only
    # ever costs latency when the server is up-but-slow — worth it for the
    # calibrated, gated dense hits over ungated FTS noise.
    inject_server_timeout_seconds: float = 3.0
    # When auto_inject fires, also record the rendered block as a
    # `memory.recall` span on the session trace so the injection is
    # auditable per-prompt in the trace UI. Off → inject silently.
    trace_recall: bool = True
    recall_top_k: int = 5
    # Minimum cross-encoder confidence for a hit to surface on reranked
    # surfaces. RRF/FTS-ordered results are rank-gated by top_k instead.
    # Calibrated against the live store (2026-06): the cross-encoder was
    # trained for task→skill routing and scores memory bodies low — exact
    # matches land 0.43-1.4 (after quality/intent multipliers), partially
    # relevant 0.2-0.3, tangential ≤0.16. The old 0.35 silently muted
    # partially-relevant injections; chronic tangential hits near the gate
    # are the feedback loop's job (ignored→decay), not the threshold's.
    recall_min_score: float = 0.25
    # MMR (maximal-marginal-relevance) diversity at the final top_k selection
    # of recall(). None → off (greedy by score). A float in [0, 1] turns it on
    # as the relevance weight: score(m) = λ·rel(m) − (1−λ)·max cosine(m, already-
    # selected), with rel min-max normalized within the candidate pool so λ
    # trades against cosine on a common scale. ~0.7 = relevance-dominant,
    # diversity as a tie-breaker. A no-op on the FTS / k=1 / no-embedding paths;
    # it only bites the dense surfaces where near-duplicate hits would otherwise
    # fill adjacent inject slots and skew the engaged/ignored feedback signal.
    inject_mmr_lambda: float | None = None
    dense_enabled: bool = True
    scope_policy: Literal["global", "per-repo", "per-repo-tagged"] = "per-repo-tagged"
    # reflect(): embedding-cosine dedup threshold, and its deterministic
    # text-similarity fallback when the embedder is unavailable.
    dedup_cosine_threshold: float = 0.92
    dedup_text_threshold: float = 0.90
    # distill(): the LLM self-scores each proposal's reusable value in
    # [0,1] (non-obvious × reusable × likely-to-recur). Below
    # `distill_min_importance` the model's own low-confidence draft is
    # dropped — selectivity over coverage. At/above `auto_approve_importance`
    # the proposal skips the human review queue and lands `active`; only the
    # gray band between the two is queued as `proposed`.
    distill_min_importance: float = 0.3
    auto_approve_importance: float = 0.85
    # distill(): at write time, when a fresh proposal makes a claim
    # incompatible with an existing memory about the same thing (a lexical
    # gray-band candidate the LLM judges CONTRADICT), retire the old row in the
    # new one's favour (status=retired, veracity=false) instead of leaving the
    # now-wrong memory live until the next reflect gray-zone pass — the
    # immediate, lexical complement to reflect's batch embedding-based check.
    # Needs the distiller's LLM. Off → distill only reinforces near-duplicates.
    distill_supersede_on_conflict: bool = True
    # distill(): after writing a proposal, deterministically file it under a
    # global meta-root by kind — `preference` → the `preferences` bucket,
    # `procedure` → the `skills` bucket (lib/topics/meta_roots.py) — so
    # skill-/preference-shaped memories get a navigable home without a manual
    # `link-topics` pass. The cheap complement to the agentic classifier
    # (`regin memory link-topics`, which routes to the precise leaf). Off →
    # distilled memories are unfiled until a classifier runs.
    distill_link_meta_roots: bool = True
    # consolidate-skills: a proven memory filed under a `skill-<slug>` meta-leaf
    # can "graduate" into that skill's own SKILL.md (a `## Lessons (from agent
    # memory)` section in the pattern source) and then be retired — the hard,
    # write-time complement to the soft `<skill_experience>` recall. Promotion
    # bar = recall_count >= `consolidate_skill_min_recall`. A `manual: true`
    # pattern is user-owned: it is NEVER auto-written, only proposed for the
    # human to apply by hand. Driven by `regin memory consolidate-skills`.
    consolidate_skills_enabled: bool = True
    consolidate_skill_min_recall: int = 3
    # reflect(): synthesis (Generative-Agents reflection). Cluster *related
    # but distinct* episodic rows (cosine in [0.55, dedup_threshold)) and ask
    # the LLM to abstract ONE higher-order rule per cluster, written as a new
    # episodic memory; sources are kept and marked 'synthesized'. Needs both
    # an embedder (to cluster) and an LLM (to abstract); a no-op without
    # either. Off → reflect only dedups / promotes / decays, never synthesises.
    synthesis_enabled: bool = True
    # reflect(): the structure layer. Roll each scope's most important
    # episodic memories (synthesis cards first — they carry the highest
    # importance — then the rest) into ONE compact maintained briefing
    # ("what this scope's sessions have learned"), persisted as a single
    # `kind="digest"` memory per scope and refreshed in place via supersede.
    # The store-derived, auto-maintained complement to the hand-curated
    # MEMORY.md. Excluded from similarity recall (standing context, read by
    # scope, never a per-query hit) and from the dedup/synthesis/decay
    # lifecycle. Needs an LLM; a no-op without one. Off by default — this is
    # the generation slice; the inject path is separate.
    digest_enabled: bool = False
    # Regenerate a scope's digest only when at least this many newer source
    # memories exist since the current digest, OR it is older than
    # `digest_max_age_days` — keeps the per-scope LLM call off the hot path.
    digest_min_new_cards: int = 3
    digest_max_age_days: float = 7.0
    # Cap on source memories (top-importance episodic) fed to the digest LLM,
    # bounding the prompt; a scope needs at least a few sources to be worth one.
    digest_max_sources: int = 20
    # reflect(): persist the embedding-cosine neighbour graph that synthesis
    # clustering already computes (and otherwise discards) as `related` edges
    # in `memory_edges`. Every pair of active embedded memories whose cosine
    # is >= `edge_floor` (and below the dedup threshold — near-identical pairs
    # are merged, not linked) becomes one undirected edge. reflect rebuilds the
    # whole `related` set each pass, so the graph tracks the live embeddings.
    # Needs an embedder (no LLM); a no-op without one. Off → no edge graph
    # (the curate UI's "Related" list falls back to on-demand cosine).
    edges_enabled: bool = True
    edge_floor: float = 0.55
    # Cap on edges recorded per memory (highest-weight kept) so a dense
    # cluster can't fan out into a hairball. 0 disables the cap.
    edge_max_per_node: int = 8
    # reflect(): when synthesis abstracts a rule from a cluster, also record
    # the cluster as a named `memory_topic` (LLM-named, the synthesised rule
    # as its summary card) with one `memory_topic_members` row per source.
    # Needs synthesis (embedder + LLM); off → synthesis still writes the rule
    # memory but no topic node is created.
    topics_enabled: bool = True
    # reflect()→synthesis: instead of minting an orphan `memory_topic`, feed
    # the synthesised rule into the *authoritative* topic-proposal review
    # queue (`.regin/topics/topic.json`). The cluster summary is embedded and
    # cosine-matched against each authoritative node; at/above
    # `reflect_topic_attach_cosine` it proposes a MERGE onto that node (and
    # links the synthesised memory to it now, since the node already exists),
    # else a CREATE candidate. Proposals are human-gated exactly like
    # external-agent ones. When this is on, the orphan `memory_topic` is NOT
    # created (this replaces it); when off, `topics_enabled` behaviour is
    # unchanged. Needs an embedder. Off by default.
    reflect_proposes_authoritative_topics: bool = False
    reflect_topic_attach_cosine: float = 0.6
    # recall(): after the ranked top_k is chosen, pull in up to
    # `recall_expand_max` one-hop `related` neighbours of the top hits that
    # aren't already selected, scored at `recall_expand_discount` x the
    # seed's score. Surfaces a memory the query didn't lexically/semantically
    # match but that sits next to one that did. Off by default — opt-in so the
    # hot auto-inject path stays unchanged. Needs the edge graph.
    recall_expand_enabled: bool = False
    recall_expand_max: int = 2
    recall_expand_discount: float = 0.5
    # recall(): weight the relevance ordering by each memory's quality —
    # importance, veracity, deliberate-recall count, and recency (recency
    # decays on a half-life, in days). Off → pure lexical/dense relevance.
    recall_quality_weighting: bool = True
    recall_recency_half_life_days: float = 30.0
    # Topic-router ↔ memory bridge (auto-inject hook). When on, the hook
    # routes the prompt through the authoritative topic graph
    # (`.regin/topics/topic.json`, keyword match) once per prompt and uses the
    # hit two ways: (1) a bounded `topic_boost_weight` multiplier on memories
    # linked to that node (see `memory_authoritative_topics`) — a soft boost
    # next to quality/intent, never a hard filter; (2) a *pointer-only*
    # `<topic_context>` block (label + intent + ref paths, capped at
    # `topic_context_max_chars`) prepended above `<recalled_experience>`. The
    # full wiki stays opt-in via the `/topic-router` skill — this only injects
    # the pointer. Off → the hook ignores the topic graph entirely.
    topic_route_inject: bool = False
    topic_boost_weight: float = 0.2
    topic_context_max_chars: int = 600
    # Close the topic-routing feedback loop. Topic injection (above) is
    # otherwise fire-and-forget: the hook prepends a `<topic_context>` banner
    # and never learns whether the route fit the prompt. When on, (1) every
    # injected topic is recorded in `topic_injections`; (2) at grade time the
    # `InjectedRelated` aspect's verdict is stamped onto that session's rows
    # (`topic_relevance_aspect` names the aspect key); (3) a route whose scored
    # injections have failed often enough is *proposed* for suppression — but
    # withholding is **human-gated** (the precision-first proposed→approved
    # contract): `_route_topic` withholds only a topic a human has marked
    # `suppressed` (`topic_route_decisions`); the thresholds below merely
    # decide which routes show as `proposed`. A topic crosses the bar at ≥
    # `topic_relevance_min_scored` scored injections *and* a fail rate ≥
    # `topic_relevance_fail_rate`; the min-volume guard keeps a topic that's
    # wrong for one prompt but right for others off the proposal list.
    # Off → topics are injected blindly, with no record or learning.
    topic_relevance_feedback: bool = True
    topic_relevance_aspect: str = "injectedrelated"
    topic_relevance_min_scored: int = 3
    topic_relevance_fail_rate: float = 0.5
    # Push each suppression *proposal* to the agent inbox (as a `warning`
    # message linking to the Memory panel) so the human gate isn't invisible.
    # One durable card per topic (keyed, deduped across grading sessions),
    # resolved when a decision is made. Off → proposals are visible only in the
    # Memory panel / `regin memory topic-feedback`.
    topic_relevance_notify: bool = True
    # Query-log term weighting for the keyword fuzzy router. Beyond the
    # always-on `wordfreq` English-frequency prior (see `lib/topics/route.py`),
    # weight each keyword DOWN by how ubiquitous it is across *this repo's own
    # past routed prompts* (`topic_injections`/`injection_events.query`, cached
    # to `.regin/topics/query_df.json`, rebuilt on the reflect sweep). A word
    # like `memory` is rare in English (the prior keeps it high) yet saturates
    # these prompts, so it carries little routing signal *here* — this is the
    # layer that sees that. The factor is bounded [`floor`, 1.0] and is a no-op
    # (1.0) until the corpus reaches `min_queries`, so a sparse log can't
    # distort routing. `min_queries` very high → effectively disabled.
    topic_route_querylog_floor: float = 0.2
    topic_route_querylog_min_queries: int = 150
    # Deliberate-recall mode (orthogonal to the always-on auto-inject hook):
    #   inline   — the main agent infers its own intent and calls the
    #              `recall` tool directly; candidates land in main context.
    #   subagent — the main agent dispatches a `memory-research` subagent
    #              that infers intent and sifts candidates in its own
    #              throwaway context, returning only a short digest, so the
    #              main context grows by the verdict, not the search.
    # Honored softly (regin doesn't own the agent loop): the auto-inject
    # block's "pull deeper" line and the deployed memory-research skill
    # reflect the active mode. See docs/agent-memory-intent-routing.md.
    recall_mode: Literal["inline", "subagent"] = "inline"
    # reflect(): retire episodic memories never *deliberately* recalled after
    # this many days — the negative half of the usefulness loop. Speculative
    # auto-inject doesn't reinforce (recall_count stays 0), so a long-aged
    # row with recall_count==0 has never proven useful. 0 disables.
    forget_after_days: int = 45
    # reflect(): the structural complement to `valid_until` time-expiry. Scan
    # active memories for concrete repo file paths (slash + known extension)
    # and, when a path no longer resolves under the memory's repo scope, flag
    # the memory for review — a 'stale_ref' validation plus a veracity demote
    # true→unknown (down-ranks via quality weighting). Never auto-retires or
    # falsifies: a regex + filesystem check is a heuristic, and the named code
    # may have merely moved while the lesson still holds. Idempotent (flags a
    # row once). A no-op on global / unregistered-repo scopes (unverifiable).
    # Off → reflect never checks whether a memory's code references still exist.
    verify_stale_refs: bool = False
    # Close the inject→usefulness loop: after a real (persisted, non-test)
    # grading run, score whether the memories auto-injected into the session
    # actually engaged its work — deterministically, no LLM (referent overlap
    # between each injected memory and the tool spans that fired after it was
    # injected; see `lib.memory.feedback`). Engaged memories earn a validation
    # + a small importance bump; ignored ones a validation only (decay is
    # reflect's job, gated on a run of ignores). The positive half of the
    # signal speculative auto-inject otherwise never gets. Needs `enabled`;
    # off → injected memories never learn whether they helped.
    feedback_on_grade: bool = True
    # reflect(): the negative half of that loop. An episodic memory that
    # earned no positive signal (zero deliberate recalls, no engaged/approved
    # validation) loses 0.1 importance per reflect run (floored at 0.1, never
    # retired from this signal) once *either* trigger fires:
    #   - decay_ignored_threshold  — it drew this many feedback 'ignored'
    #     verdicts. Produced only at grade time, so on its own this is inert
    #     for the common session that never triggers a grade.
    #   - decay_injected_threshold — it was auto-injected this many times with
    #     zero reinforcement. Read from `injection_events`, which is recorded
    #     for *every* inject, so this keeps the loop alive without a grade.
    # Set a trigger to 0 to disable that half. The injected threshold sits a
    # little higher by default because one non-reinforcement is weaker evidence
    # than an explicit 'ignored' verdict.
    decay_ignored_threshold: int = 5
    decay_injected_threshold: int = 8
    # reflect(): the *positive* half made always-on, symmetric with the
    # injected-decay signal above. `feedback_on_grade` scores engagement only
    # for the rare graded session, so a memory that genuinely guided dozens of
    # ungraded sessions earned no recorded credit and stayed decay-eligible.
    # The pending sweep stamps every still-unscored injection event from a
    # *finished* session (its `injected_at` older than `feedback_lag_minutes`,
    # so the post-injection spans have landed) with an engaged/ignored verdict
    # — validation-only, no importance bump, so densifying can't inflate the
    # importance axis. Off → engagement credit fires only at grade time.
    score_pending_on_reflect: bool = True
    feedback_lag_minutes: int = 120
    # idf-weight the engaged/ignored verdict by referent *specificity*. The
    # binary rule counts any referent reappearing downstream as engagement,
    # but referents common across the corpus (`cli/regin.py`, `db/regin.db`)
    # reappear in nearly every session regardless of whether the memory
    # steered the work — inflating broad-referent memories and deflating ones
    # whose only-when-relevant referents (`_find_state_evidence`) keep them
    # mis-injected. With this on, a match scores engaged only when the matched
    # referents' summed normalised idf (1.0 = unique, 0 = corpus-saturating)
    # clears the threshold. 0 disables (binary rule); the verdict also falls
    # back to binary until the active corpus reaches `_IDF_MIN_CORPUS` rows,
    # where document frequencies stop being noise.
    engagement_idf_min_weight: float = 0.5
    # decay spare/trigger by engaged-*rate*, not a binary "ever engaged" flag.
    # Once the sweep above densifies the signal, a single engagement would
    # otherwise make a heavily-ignored memory (e.g. 6 engaged / 55 ignored)
    # un-decayable. A memory is spared when engaged/(engaged+ignored) ≥
    # `engage_spare_rate` (given ≥ `engage_min_volume` scored injects); a low
    # rate at volume instead *forces* decay even with reinforcement. Below the
    # volume floor, any engagement still spares (benefit of the doubt). Set
    # `engage_spare_rate` to 0 to fall back to the old binary behavior.
    engage_spare_rate: float = 0.4
    engage_min_volume: int = 4
    # Cap on stored query exemplars per (topic, model, polarity) — bounds the
    # per-route kNN behind topic-route suppression/protection (`TopicExemplar`).
    negative_max_per_memory: int = 10
    # Topic-route query-local suppression/protection: when a `<topic_context>`
    # banner is graded `fail` (`InjectedRelated`), the prompt embedding is stored as a
    # topic negative (`TopicNegative`). At route time the banner is *withheld*
    # when the incoming query's max cosine to that topic's negatives clears this
    # threshold — query-local suppression replacing the binary global fail-rate
    # gate, overridable by a human `allowed` pin. Computed server-side (the hook
    # is model-free), so it engages only on the dense/server recall path. 0
    # disables (no route suppression by negatives, and none are recorded).
    topic_negative_suppress_sim: float = 0.0
    # Agentic distill: read-only tools the distiller subprocess may use so
    # it can self-fetch the session's trace (the compact `--index` catalog +
    # individual span content) and grep the repo's standing docs to drop
    # proposals that merely restate what CLAUDE.md / ARCHITECTURE.md /
    # README.md already document. Passed as `claude --allowedTools`; empty →
    # the distiller can't investigate and works from the embedded hints
    # alone. Mirrors `settings.grader.judge_allowed_tools`.
    # Read + Bash(grep:*) are intentionally read-only and narrowly scoped:
    # Read lets the distiller open individual doc files; grep lets it search
    # across docs/ and the root Markdown files. No write tools are included.
    distill_allowed_tools: list[str] = [
        "Bash(.venv/bin/python cli/regin.py trace dump:*)",
        "Bash(.venv/bin/python cli/regin.py trace span:*)",
        "Read",
        "Bash(grep:*)",
        "Bash(git log:*)",
        "Bash(git show:*)",
    ]


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


class AgentMessagesConfig(BaseModel):
    """The `send_to_user` agent → human channel (inbox + push channels).

    Persistence and the in-app inbox are always on; outbound **push
    channels** are opt-in. Each channel is gated by its own
    `*_min_severity`: only messages at or above that severity in
    `lib.orm.models.agent_messages.MESSAGE_TYPES` are delivered, so a
    background run can reach you on a `blocker` without spamming you on
    every `progress` line. A message fans out to *every* configured
    channel whose gate it clears.

    Channels live in `lib/agent_messages/push/`; each reads its own flat
    fields below. Adding one is a new `PushChannel` subclass + a registry
    entry + its config fields here — no change to the dispatch path.

    `base_url` is woven into each payload so the notification links
    straight back to the originating session in the regin UI.
    """

    base_url: str = "http://127.0.0.1:8321"

    # ── Generic webhook channel (ntfy / Slack incoming hook / phone) ──
    webhook_url: str | None = None
    webhook_min_severity: _SEVERITY = "warning"
    webhook_timeout_seconds: float = 5.0

    # ── Telegram channel (Bot API sendMessage) ──
    # Create a bot via @BotFather for the token; `chat_id` is your user or
    # group id (talk to the bot once, then read it from getUpdates).
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_min_severity: _SEVERITY = "warning"
    telegram_timeout_seconds: float = 5.0

    # ── Lark / Feishu channel (custom-bot incoming webhook) ──
    # In a Lark group: Settings → Bots → Add Bot → Custom Bot → copy the
    # webhook URL into `lark_webhook_url`. If you enable the bot's
    # "signature verification", put that secret in `lark_secret` (the
    # channel then signs each request); leave it None otherwise.
    lark_webhook_url: str | None = None
    lark_secret: str | None = None
    lark_min_severity: _SEVERITY = "warning"
    lark_timeout_seconds: float = 5.0

    # ── Interaction-event pushes (opt-in) ──
    # Beyond agent-authored `send_to_user`, surface the moments where the
    # agent is *blocked waiting on you* as inbox cards that also fan out to
    # the channels above. Off by default — permission prompts can be frequent.
    # `push_permission_events`: a pending permission prompt / AskUserQuestion
    #   (recorded as a `blocker`). `push_plan_events`: a plan ready for review
    #   on ExitPlanMode (recorded as a `warning`). Each still rides the
    #   per-channel severity gate, so a channel set to `blocker` gets prompts
    #   but not plans.
    push_permission_events: bool = False
    push_plan_events: bool = False

    # ── Event bus overrides ──
    # Per-kind enable/disable for the declared notifiable events in
    # `lib.agent_messages.events.REGISTRY` (proposal.ready, content.drift,
    # grade.finished, …). A `{kind: bool}` here overrides that kind's
    # registry default; kinds absent from the map keep their default (the two
    # interaction kinds also honor the `push_*_events` booleans above for
    # back-compat). Enumerate the full catalog with `regin events list`.
    events: dict[str, bool] = Field(default_factory=dict)

    # ── Retention (opt-in) ──
    # The inbox is otherwise grow-forever. When `retention_days` is set,
    # messages older than that are hard-deleted automatically after each
    # write (see `store._enforce_retention`). None = keep forever (default,
    # original behavior). `retention_keep_pinned` shields pinned cards from
    # the auto-prune. Manual `regin messages prune` is always available.
    retention_days: int | None = None
    retention_keep_pinned: bool = True


class TopicEvolutionConfig(BaseModel):
    """Code-driven topic/memory co-evolution (`lib/topics` drift loop).

    The substrate for letting the approved topic graph and agent memory
    follow the code without a human authoring every proposal. Everything
    here defaults **off**: `evolution_enabled` only unlocks the machinery,
    and `mechanical_autoapply` separately gates the one tier that writes
    without review — ref renames into the gitignored `topic.local.json`
    overlay, never the human-approved `topic.json`. `auto_spawn_agents`
    separately gates launching the external drafting agent for refresh
    proposals (off by default even when evolution is on — spawning is a cost).

    `content_drift_cosine` is the similarity floor below which a topic's
    ref files are judged to have drifted from its wiki narrative.
    `drift_proposal_batch_max` caps proposals emitted per evolve pass so a
    large commit can't spawn an unbounded number of drafting agents.
    `auto_proposal_expire_days` retires unreviewed, never-routed
    auto-proposals so the review queue can't rot.
    """

    evolution_enabled: bool = False
    mechanical_autoapply: bool = False
    auto_spawn_agents: bool = False
    content_drift_cosine: float = 0.6
    drift_proposal_batch_max: int = 3
    auto_proposal_expire_days: int = 14
    # When set, every completed proposal run (initial draft or regenerate)
    # gets an LLM-written review note attached as a `review_note` feedback
    # thread — a regenerate/accept/dismiss recommendation that rides the
    # existing feedback machinery into the next run. Off by default and
    # separately gated (a review note is an external-agent call, a cost),
    # so it stays off even when the rest of evolution is on. The manual
    # endpoint/CLI is ungated.
    auto_review_notes: bool = False

    # Proposal runs complete by the drafting agent calling
    # `regin topics proposal-finish <id>` as its final step (notify-on-finish)
    # rather than the server blocking on the subprocess up to a fixed timeout.
    # `proposal_run_timeout_seconds` is the server-side hard ceiling the spawn
    # may block before it is reaped; 0 means no ceiling — rely entirely on the
    # finish signal plus the trace-based reaper, so a long draft is never
    # killed mid-flight. `proposal_stranded_grace_seconds` is how long a
    # non-terminal run with no live watcher (e.g. after `regin serve` restarts
    # mid-run) may stay quiet before `reap_stranded_proposal_runs` fails it.
    proposal_run_timeout_seconds: int = 0
    proposal_stranded_grace_seconds: int = 1800


# Project-root-relative paths — fixed by where this file lives.
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
_CONFIG_DIR: Path = _PROJECT_ROOT / "config"
_SHARED_SETTINGS_PATH: Path = _CONFIG_DIR / "settings.json"
_LOCAL_SETTINGS_PATH: Path = _CONFIG_DIR / "settings.local.json"


def _xdg_data_home() -> Path:
    """XDG_DATA_HOME or its portable default (`~/.local/share`)."""
    env = os.environ.get("XDG_DATA_HOME")
    return Path(env) if env else Path.home() / ".local" / "share"


def _default_data_dir() -> Path:
    """Honour REGIN_DATA_DIR for the user-local data root. Evaluated at
    class-definition time so `Settings()` with no env picks up the right
    path from the process env."""
    env = os.environ.get("REGIN_DATA_DIR")
    return Path(env) if env else _xdg_data_home() / "regin"


_DATA_DIR_DEFAULT: Path = _default_data_dir()


class Settings(BaseSettings):
    """Declarative view of every configurable value regin reads at boot."""

    model_config = SettingsConfigDict(
        env_prefix="REGIN_",
        extra="ignore",
        case_sensitive=False,
        # JSON sources plug in via `settings_customise_sources` below.
    )

    # ── Paths ────────────────────────────────────────────────
    # The repository checkout root. Fixed by where this file lives, not
    # user-configurable; exposed as a field (rather than a constant) so
    # callers read it via `settings.project_root` and tests can redirect
    # it for isolation.
    project_root: Path = Field(default_factory=lambda: _PROJECT_ROOT)

    # The user-local data root. Overridable via REGIN_DATA_DIR.
    data_dir: Path = Field(default_factory=_default_data_dir)

    # Where procedure guides (patterns) live. Default:
    # $REGIN_DATA_DIR/patterns. Honours REGIN_PATTERNS_DIR.
    patterns_dir: Path = Field(default=_DATA_DIR_DEFAULT / "patterns")

    # GritQL rule sources + generated indexes. Default:
    # $REGIN_DATA_DIR/grit. Honours REGIN_GRIT_DIR. Used as the default
    # grit dir for a `rule_engines` entry of kind 'grit' that doesn't set
    # its own `grit_dir`.
    grit_dir: Path = Field(default=_DATA_DIR_DEFAULT / "grit")

    # Rule engines (linters, structural rewriters) regin should load.
    # An empty list means regin runs as a generic harness with no rule
    # enforcement at all (unless `bundle_autoload` discovers a bundle).
    rule_engines: list[RuleEngineConfig] = Field(default_factory=list)

    # When true, scan `patterns_dir/*/regin-bundle.{yaml,json}` and load
    # each as a `BundleEngine`. Explicit `rule_engines` entries with the
    # same `id` always win — auto-discovered entries only fill gaps.
    bundle_autoload: bool = True

    # Config-only language→file-extension overrides for the PostToolUse
    # rule gate (hook_manager/handlers/rule_check.py). Maps a language id
    # to the file extensions (leading dot, e.g. ".kt") that identify it.
    # Lets you point a rule engine at a brand-new language with no code
    # change: declare the id→extensions here and list the id in an
    # engine's `language_ids`. Consulted BEFORE the lib/languages registry
    # and the handler's built-in fallback map, so it overrides either.
    language_extensions: dict[str, list[str]] = Field(default_factory=dict)

    # User-curated tag definitions YAML.
    tags_path: Path = Field(default=_DATA_DIR_DEFAULT / "config" / "tags.yaml")

    # ── Activity logs (per-feature JSONL via loguru) ─────────
    # Where activity log files live. Default: <data_dir>/logs.
    # Honours REGIN_LOG_DIR. See lib/activity_log.py.
    log_dir: Path = Field(default=_DATA_DIR_DEFAULT / "logs")
    # Age-based retention for rotated activity logs (days).
    log_retention_days: int = 14
    # Size cap per pre-rotation file. Default 50 MB.
    log_max_bytes_per_file: int = 50 * 1024 * 1024
    # Feature registry — typo guard. Unknown features get tagged
    # `feature=other` with a one-time stderr warning. All features
    # share `regin.log`; this list controls validation only.
    activity_log_features: list[str] = Field(
        default_factory=lambda: [
            "hooks", "patterns", "sync", "web", "cli", "rules",
            "trace_ingest", "topics", "auth", "rebuild",
            "agent_messages", "agent_bridge", "memory", "grader", "goal", "gate", "prompts", "other",
        ]
    )

    # Auto-tagging rules YAML.
    auto_tag_rules_path: Path = Field(default=_DATA_DIR_DEFAULT / "config" / "auto_tag_rules.yaml")

    # Per-user overlay for PostToolUse payload schemas. The validator
    # merges <overlay>/<agent>/<tool>.schema.json on top of the repo-
    # tracked baseline at lib/trace/payload_schemas/<agent>/. Ratifying
    # a drift finding writes to the overlay, never to the baseline, so
    # `git pull` never conflicts with local schema customizations.
    payload_schemas_overlay_dir: Path = Field(
        default=_DATA_DIR_DEFAULT / "payload_schemas",
    )

    # Master switch for the harness Diagnostics surface: PostToolUse
    # payload schema validation, drift recording, and ~/.claude/
    # hook-payloads.jsonl appends. Default OFF — this is a maintainer
    # tool, and common users shouldn't pay the per-hook overhead they
    # didn't ask for. Toggleable from the Diagnostics page or settings.
    diagnostics_enabled: bool = False

    # ── Provider deploy targets ────────────────────────────
    active_provider: Literal["claude", "codex", "generic", "kimi"] = "claude"
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)

    # When false, only the `claude` provider (plus the active provider, if it
    # was explicitly switched away from claude) is exposed to UI surfaces like
    # SettingsView / /api/hooks / /api/providers. Flip to true to surface the
    # experimental `codex` and `generic` providers.
    experimental_providers: bool = False

    # Gate for the SKILL.md concealment-experiments feature. When false
    # (default), the Experiments nav link, the pattern-detail Experiments
    # tab + create-experiment affordance, and the /experiments routes are
    # hidden in the UI. The backend table and conceal filter remain so
    # any rows already written still drive deploy behavior.
    experimental_conceal: bool = False

    # Gate for the dense (semantic) pattern search UI on the Patterns
    # page. When false (default), the "Dense search" toggle, query
    # input, and Route button are hidden. The /patterns/route backend
    # remains reachable so any direct callers keep working.
    experimental_dense_search: bool = False

    # Hybrid pattern-search reranker threshold. The SkillRouter
    # cross-encoder pass runs only when the fused candidate set has at
    # least this many items. Default = 1 (rerank-always, matching
    # SkillRouter's evaluated pipeline). Raise to skip rerank on tiny
    # candidate sets where it adds latency without lift.
    dense_rerank_min_corpus: int = 1

    # When False, `pattern_router.route()` skips the SkillRouter dense
    # leg and ranks via BM25/FTS5 only — no embedding model load, no
    # ~1.2 GB download, no rerank. Ablation at this corpus size
    # (scripts/ablate_pattern_router.py) shows top-1 unchanged vs the
    # full hybrid; flip back to True once the pattern catalog grows
    # past ~100 overlapping items. Distinct from
    # `experimental_dense_search`, which is a UI feature gate.
    pattern_router_dense_enabled: bool = True

    # Legacy Claude-specific path knob kept for back-compat while
    # provider adapters are introduced.
    skills_dir: Path = Field(default_factory=lambda: Path.home() / ".claude" / "skills")

    # ── Discovery ───────────────────────────────────────────
    # Explicit list of registered repository paths. Each entry MUST point
    # at a git working tree; managed through the /repos web UI or the
    # `regin add-repo` / `regin remove-repo` CLI commands.
    repo_paths: list[Path] = Field(default_factory=list)

    # ── Web ─────────────────────────────────────────────────
    web_port: int = 8321

    # ── Mode + external services ────────────────────────────
    # 'standalone' = local SQLite for auth/audit. 'shared' = MySQL.
    mode: Literal["standalone", "shared"] = "standalone"

    # MySQL URL (shared mode). Honours REGIN_DATABASE_URL.
    database_url: str | None = None

    # Optional regin-skillhub server (for `pattern promote`).
    skillhub_url: str = "http://127.0.0.1:8322"

    # Topic proposals are drafted by an external tool-using agent.
    topic_proposal_external_agents: dict[str, TopicProposalExternalAgent] = Field(default_factory=dict)

    # How many non-latest, non-pinned `graph_snapshots` rows to retain
    # per repo. `apply_diff` prunes beyond this after every accept/merge/
    # replace. Pinned rows and `is_latest=1` always survive. Set to 0 to
    # disable inline pruning entirely.
    topic_snapshot_keep: int = 50

    # ── Rule trigger health ─────────────────────────────────
    # Thresholds for classifying each rule as active / noisy / dead on
    # the /trace/triggers tab. Editable via /settings → rule-trigger
    # thresholds card (PR-3 onward).
    rule_trigger_thresholds: RuleTriggerThresholds = Field(default_factory=RuleTriggerThresholds)

    # ── Trace ───────────────────────────────────────────────
    # Capture each assistant turn's response text into session_spans
    # (`assistant_response` spans). Off-switch for users who don't want
    # response text persisted in the trace DB.
    capture_assistant_response: bool = True
    # Per-response byte cap. Spans are bulk-loaded with the session
    # detail response, so the cap stays conservative — anything larger
    # is truncated with a marker before being POSTed to /api/session-spans.
    assistant_response_max_bytes: int = 50_000
    # User-submitted images in prompts: per-image byte cap (drop if over)
    # and per-prompt image-count cap.
    capture_prompt_images: bool = True
    prompt_image_max_bytes: int = 5_000_000   # 5 MB
    prompt_images_max_count: int = 10

    # ── Agent → human messages (send_to_user inbox + webhook) ─
    agent_messages: AgentMessagesConfig = Field(default_factory=AgentMessagesConfig)

    # ── Trace retention (opt-in prune of superseded pending spans) ──
    trace_retention: TraceRetentionConfig = Field(default_factory=TraceRetentionConfig)

    # ── Cross-session agent memory (lib/memory, separate DB) ──
    agent_memory: AgentMemoryConfig = Field(default_factory=AgentMemoryConfig)

    # ── Post-hoc session rubric grader (lib/grader) ───────────
    grader: GraderConfig = Field(default_factory=GraderConfig)

    # ── Code-driven topic/memory co-evolution (lib/topics drift) ──
    topic_evolution: TopicEvolutionConfig = Field(
        default_factory=TopicEvolutionConfig)

    # Per-model context-window overrides (model id -> token count). Merged
    # on top of the built-in table in `lib/tokens/model_windows.py`. Use
    # this to track windows for in-house or preview models, or to correct
    # the default if Anthropic ships a new window size mid-cycle.
    model_context_windows: dict[str, int] = Field(default_factory=dict)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        """Layer: env > settings.local.json > settings.json > defaults."""
        shared = JsonConfigSettingsSource(settings_cls, json_file=_SHARED_SETTINGS_PATH)
        local = JsonConfigSettingsSource(settings_cls, json_file=_LOCAL_SETTINGS_PATH)
        return (init_settings, env_settings, local, shared, file_secret_settings)

    def model_post_init(self, _context) -> None:
        """If path fields were left at their original defaults, rewrite them
        relative to the resolved `data_dir`. Users who set only
        REGIN_DATA_DIR expect every downstream path to follow."""
        if self.data_dir != _DATA_DIR_DEFAULT:
            self._rebase_default_paths()

        # Expand `~` in path fields so downstream `os.path.join(str(path), ...)`
        # doesn't accidentally produce literal `~` components.
        for field in ("patterns_dir", "grit_dir", "tags_path",
                      "auto_tag_rules_path", "skills_dir", "data_dir",
                      "log_dir", "payload_schemas_overlay_dir"):
            current = getattr(self, field)
            expanded = Path(os.path.expanduser(str(current)))
            if expanded != current:
                object.__setattr__(self, field, expanded)

        # Repo paths may be specified as strings in settings.json; expand `~`.
        self._expand_repo_paths()
        return

    # Tuple of (field_name, subpath under _DATA_DIR_DEFAULT) for every
    # path that should follow data_dir when only REGIN_DATA_DIR was set.
    _DEFAULT_REBASE_FIELDS = (
        ("patterns_dir", ("patterns",)),
        ("grit_dir", ("grit",)),
        ("tags_path", ("config", "tags.yaml")),
        ("auto_tag_rules_path", ("config", "auto_tag_rules.yaml")),
        ("log_dir", ("logs",)),
        ("payload_schemas_overlay_dir", ("payload_schemas",)),
    )

    def _rebase_default_paths(self) -> None:
        for field, parts in self._DEFAULT_REBASE_FIELDS:
            default_value = _DATA_DIR_DEFAULT.joinpath(*parts)
            if getattr(self, field) == default_value:
                object.__setattr__(self, field, self.data_dir.joinpath(*parts))

    def _expand_repo_paths(self) -> None:
        expanded_repos = [Path(os.path.expanduser(str(p))) for p in self.repo_paths]
        if expanded_repos != list(self.repo_paths):
            object.__setattr__(self, "repo_paths", expanded_repos)

        # Provider path overrides are optional; expand any ~ values.
        for provider_id, override in self.providers.items():
            for field in (
                "skills_dir",
                "plans_dir",
                "traces_dir",
                "hook_settings_path",
                "hook_manager_config_path",
                "hook_payload_log_path",
                "transcript_projects_dir",
            ):
                current = getattr(override, field)
                if current is None:
                    continue
                expanded = Path(os.path.expanduser(str(current)))
                if expanded != current:
                    setattr(override, field, expanded)


# Module-level singleton. Re-exported so callers can do:
#
#   from lib.settings import settings
#
# If a caller needs a fresh parse (e.g. after `save_settings()` updated
# the JSON files), they should import `reload_settings` and call it.
settings = Settings()


def reload_settings() -> Settings:
    """Re-read env + JSON files and refresh the module singleton IN PLACE.

    The `settings` object's identity is preserved, so modules and tests
    that captured `from lib.settings import settings` at import time see
    the refreshed values — and `monkeypatch.setattr(settings, ...)` always
    lands on the live instance. Returns the same, mutated instance.
    """
    fresh = Settings()
    for _name in type(settings).model_fields:
        object.__setattr__(settings, _name, getattr(fresh, _name))
    return settings


# ── Config-file paths for the settings.json CRUD below ────────────
#
# Kept as module-level constants because the CRUD helpers
# (save_settings / _load_settings / get_current_values), the /settings
# web UI, and several tests reference and monkeypatch them. Every other
# legacy constant was eliminated — callers now read the typed `settings`
# instance directly (e.g. `settings.patterns_dir`, `settings.mode`).

CONFIG_DIR: str = str(_CONFIG_DIR)
SETTINGS_PATH: str = str(_SHARED_SETTINGS_PATH)
SETTINGS_LOCAL_PATH: str = str(_LOCAL_SETTINGS_PATH)


# Settings that are machine-specific (not shared via git). Keyed by raw
# key name because `save_settings(scope)` and web/blueprints/settings.py
# key on the string. `diagnostics_enabled` is per-machine on purpose: a
# laptop can run diagnostics ON while a shared deploy stays OFF, and
# routing it through local keeps the diagnostics pill and the /settings
# page writing to the same file.
LOCAL_SETTINGS_KEYS: set[str] = {
    "repo_paths", "active_provider", "providers",
    "skills_dir", "skillhub_url",
    "patterns_dir", "grit_dir", "tags_path",
    "auto_tag_rules_path",
    "diagnostics_enabled",
}


# Schema the /settings page renders. Each entry is (key, default,
# description); the default mirrors the corresponding Settings field.
# NOTE: `repo_paths` is intentionally omitted — the /repos UI (and
# `regin add-repo`/`remove-repo`) manage it.
SETTINGS_SCHEMA: list[tuple[str, object, str]] = [
    ("web_port", 8321,
     "Web dashboard port"),
    ("active_provider", "claude",
     "Primary agent provider (claude, codex, generic, kimi). Used as the default for single-provider operations and as the source of truth when no explicit provider is given."),
    ("experimental_providers", False,
     "Surface experimental agent providers (codex, generic) in the Settings hook-manager UI. When off, only claude is shown."),
    ("experimental_conceal", False,
     "Surface the SKILL.md concealment-experiments UI (Experiments nav link, pattern-detail tab, /experiments routes). When off, the broken-by-design feature is hidden."),
    ("experimental_dense_search", False,
     "Surface the dense (semantic) pattern search UI on the Patterns page (toggle, query input, Route button). When off, only the standard tag/category filters are shown."),
    ("dense_rerank_min_corpus", 1,
     "Hybrid pattern-search reranker threshold: the SkillRouter cross-encoder runs only when the fused candidate set has at least this many items. Default 1 = rerank always."),
    ("skills_dir", str(Path.home() / ".claude" / "skills"),
     "Claude Code skills deploy directory"),
    ("mode", "standalone",
     "Server mode: standalone (local SQLite) or shared (MySQL for users/audit)"),
    ("skillhub_url", "http://127.0.0.1:8322",
     "Base URL of the optional regin-skillhub server (for `pattern promote`)"),
    ("patterns_dir", str(_xdg_data_home() / "regin" / "patterns"),
     "Directory where procedure guides (patterns) are stored (user-local data)"),
    ("grit_dir", str(_xdg_data_home() / "regin" / "grit"),
     "Directory where GritQL rule sources and generated indexes live (user-local data)"),
    ("tags_path", str(_xdg_data_home() / "regin" / "config" / "tags.yaml"),
     "Path to user-curated tag definitions YAML (user-local data)"),
    ("auto_tag_rules_path", str(_xdg_data_home() / "regin" / "config" / "auto_tag_rules.yaml"),
     "Path to auto-tagging rules YAML: repo-name patterns, annotations, base classes (user-local data)"),
    ("capture_assistant_response", True,
     "Capture each assistant turn's response text into session_spans (assistant_response spans)"),
    ("assistant_response_max_bytes", 50_000,
     "Per-response byte cap before truncation (spans are bulk-loaded with session detail)"),
    ("diagnostics_enabled", False,
     "Maintainer Diagnostics: payload schema validation, drift detection, and raw payload log appends. Default off; turn on if you're debugging the harness or tracking Anthropic payload-shape changes."),
]


# ── settings.json CRUD (backs the /settings web UI) ───────────────

# Keys whose values are file paths — `~` expanded at read time.
_PATH_KEYS: set[str] = {
    "repo_paths", "skills_dir", "patterns_dir",
    "grit_dir", "tags_path", "auto_tag_rules_path",
    "log_dir", "payload_schemas_overlay_dir", "data_dir",
}


def _load_json(path: str) -> dict:
    """Load a JSON file, returning {} on error."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _load_settings() -> dict:
    """Merged settings dict: shared (settings.json) + local overrides.

    Returns a plain dict (not the Settings class) because call sites
    dump this to JSON for the dashboard. Prefer the typed `settings`
    instance in new code.
    """
    shared = _load_json(SETTINGS_PATH)
    local = _load_json(SETTINGS_LOCAL_PATH)
    return {**shared, **local}


def _expand_paths(value):
    """Expand `~` in a path string or list of path strings."""
    if isinstance(value, str):
        return os.path.expanduser(value)
    if isinstance(value, list):
        return [os.path.expanduser(v) if isinstance(v, str) else v for v in value]
    return value


def _get(key: str, default):
    """Read a setting from the merged JSON files, preferring the value
    found there over `default`. Path values get `~` expansion.

    Back-compat for call sites wanting a bespoke setting not on the typed
    Settings class. Prefer adding a typed field for new settings.
    """
    value = _load_settings().get(key, default)
    if key in _PATH_KEYS:
        return _expand_paths(value)
    return value


def _save_to_file(path: str, updates: dict) -> None:
    """Merge updates into a JSON file."""
    existing = _load_json(path)
    existing.update(updates)
    with open(path, "w") as f:
        json.dump(existing, f, indent=2)


def save_settings(updates: dict, scope: str = "auto") -> None:
    """Merge updates into the appropriate settings file and write back.

    scope: 'shared' → settings.json (git-tracked), 'local' →
    settings.local.json (gitignored), 'auto' → route each key by
    LOCAL_SETTINGS_KEYS.

    The process-wide `settings` singleton is refreshed via
    `reload_settings()` so long-running web processes pick up UI edits.
    """
    os.makedirs(CONFIG_DIR, exist_ok=True)

    if scope == "auto":
        shared_updates = {k: v for k, v in updates.items() if k not in LOCAL_SETTINGS_KEYS}
        local_updates = {k: v for k, v in updates.items() if k in LOCAL_SETTINGS_KEYS}
        if shared_updates:
            _save_to_file(SETTINGS_PATH, shared_updates)
        if local_updates:
            _save_to_file(SETTINGS_LOCAL_PATH, local_updates)
    elif scope == "local":
        _save_to_file(SETTINGS_LOCAL_PATH, updates)
    else:
        _save_to_file(SETTINGS_PATH, updates)

    try:
        reload_settings()
    except Exception:
        pass


def get_current_values() -> list[dict]:
    """Return all settings with current values and metadata (for the UI)."""
    shared = _load_json(SETTINGS_PATH)
    local = _load_json(SETTINGS_LOCAL_PATH)
    merged = {**shared, **local}
    result = []
    for key, default, description in SETTINGS_SCHEMA:
        value = merged.get(key, default)
        is_local = key in LOCAL_SETTINGS_KEYS
        result.append({
            "key": key,
            "default": default,
            "value": value,
            "description": description,
            "is_list": isinstance(default, list),
            "is_bool": isinstance(default, bool),
            "overridden": key in merged,
            "scope": "local" if is_local else "shared",
        })
    return result


__all__ = [
    "AgentMemoryConfig",
    "AgentMessagesConfig",
    "GraderAspect",
    "GraderConfig",
    "ProviderPathOverrides",
    "ProviderConfig",
    "RuleEngineConfig",
    "RuleTriggerThresholds",
    "Settings",
    "TopicProposalExternalAgent",
    "settings",
    "reload_settings",
    # Config-file paths + settings.json CRUD (relocated from lib/config.py).
    "CONFIG_DIR", "SETTINGS_PATH", "SETTINGS_LOCAL_PATH",
    "LOCAL_SETTINGS_KEYS", "SETTINGS_SCHEMA",
    "save_settings", "get_current_values",
    "_load_settings", "_get",
]
