"""Agent-memory and topic-evolution blocks of `Settings`."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


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
    # reflect(): the "dream" — the ONE agentic LLM stage per reflect run. A
    # single call receives a bounded evidence pack (every pending working row
    # with its co-retrieval neighbours + suspect episodic pairs) and returns
    # one plan: promote/hold/drop/merge per row, contradict/obsolete/distinct
    # per pair, optional synthesis. Off (or no LLM) → every surviving working
    # row is blind-promoted and nothing else is judged.
    dream_enabled: bool = True
    # Read-only tools the dream subprocess may use to pull evidence beyond
    # the pack (`claude --allowedTools`); mirrors `distill_allowed_tools`.
    dream_allowed_tools: list[str] = [
        "Bash(.venv/bin/python cli/regin.py memory list:*)",
        "Bash(.venv/bin/python cli/regin.py memory recall:*)",
    ]
    # Cap on suspect episodic pairs offered to one dream. Candidates are
    # same-scope pairs naming a common concrete repo file path (real
    # contradictions are low-cosine, so no similarity gate), excluding pairs
    # already presented (`memory_pair_checks`).
    contradiction_budget: int = 8
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
    # Gate on the dream's *destructive* row verdicts. Off → a `drop`/`merge`
    # verdict degrades to `hold` (the row simply stays working); the model can
    # only ever promote or hold. On → `drop`/`merge` retire the row via
    # reversible supersede. Off by default: a noisy categorical must not
    # hard-control a destructive action without opt-in.
    promote_allow_retire: bool = False
    # reflect(): persist the embedding-cosine neighbour graph as `related` edges
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
    # queue (`.regin/topics/topics/`). The cluster summary is embedded and
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
    # (`.regin/topics/topics/`, keyword match) once per prompt and uses the
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


class TopicEvolutionConfig(BaseModel):
    """Code-driven topic/memory co-evolution (`lib/topics` drift loop).

    The substrate for letting the approved topic graph and agent memory
    follow the code without a human authoring every proposal. Everything
    here defaults **off**: `evolution_enabled` only unlocks the machinery,
    and `mechanical_autoapply` separately gates the one tier that writes
    without review — ref renames into the gitignored `topic.local.json`
    overlay, never the human-approved base graph. `auto_spawn_agents`
    separately gates launching the external drafting agent for refresh
    proposals (off by default even when evolution is on — spawning is a cost).

    `content_drift_cosine` is the similarity floor below which a topic's
    ref files are judged to have drifted from its wiki narrative. It only
    applies to digest rows carrying an embedding but no wiki-anchor set;
    the default is calibrated to the SkillRouter embedder, whose cosines
    compress into 0.97–1.0 (trivial edits ≥ 0.999, material rewrites
    ~0.97), so anything much lower spares every change.
    `drift_proposal_batch_max` caps proposals emitted per evolve pass so a
    large commit can't spawn an unbounded number of drafting agents.
    `auto_proposal_expire_days` retires unreviewed, never-routed
    auto-proposals so the review queue can't rot.
    """

    evolution_enabled: bool = False
    mechanical_autoapply: bool = False
    auto_spawn_agents: bool = False
    content_drift_cosine: float = 0.995
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

    # Run the drafting agent through the Agent SDK tier (`lib/agent_sdk`)
    # instead of `subprocess.Popen` + `communicate()`. Inert unless
    # `agent_sdk.enabled` is also on: the SDK tier launches the user's own
    # `claude`, so a run bound to a different CLI keeps the subprocess path
    # regardless. What it buys is a run that outlives the request — a durable
    # `agent_runs` row, live spans, and `/live` steering/stop — instead of a
    # `Popen` handle on one thread's stack.
    proposal_sdk_runner: bool = True
