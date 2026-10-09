// The four Topics sub-pages (F154 G1). The prototype does not draw them, so
// their text is the pre-pixel views' text, re-cut into DocBlock sections.
import { C, N, P, T } from './blocks.js'

const COMMAND = ['COMMAND', 'WHAT IT DOES']
const COMMAND_GRID = 'minmax(220px,1fr) minmax(240px,1.6fr)'

export const ROUTING = [
  ['The matcher', [
    P('Routing is the read path of the [topic graph](/topics). It is a keyword router, not a semantic search: given a query it either finds a topic it can *explain* matching — down to the exact keywords that fired — or it honestly returns nothing. No embeddings, no inference cost, no coincidental matches dressed up as relevance.'),
    P('Five strategies run in priority order; the first hit wins. The four precise strategies each scan every topic before the fuzzy fallback gets a turn, so an exact alias match on one topic always beats a loose substring match on another.'),
    T(['#', 'STRATEGY', 'MATCHES WHEN'], '40px minmax(170px,1fr) minmax(240px,2fr)', [
      ['1', 'Alias / label — exact', 'The whole query equals a topic id, label, or alias.'],
      ['2', 'Ref path — exact', 'The query equals one of a topic’s reference file paths.'],
      ['3', 'Ref path — substring', 'The query appears inside a reference path (merge.py, blueprints/topics).'],
      ['4', 'Label / intent — substring', 'The query appears in the topic’s id + label + intent + aliases text.'],
      ['5', 'Fuzzy multi-keyword', 'Fallback for prose: at least 2 distinct informative keywords must land on the same topic.'],
    ], '520px'),
    P('A miss returns `status: "unmatched"` with empty refs. That\'s deliberate: for prose with no topical vocabulary, no context is more useful than the wrong context.'),
  ]],
  ['Keyword weighting', [
    P('The fuzzy fallback scores keyword overlap by *informativeness*, not raw hit count — one rare domain term outweighs three common words landing by coincidence. Three layers stack:'),
    T(['LAYER', 'WHAT IT CORRECTS FOR'], 'minmax(170px,1fr) minmax(260px,2.2fr)', [
      ['English-rarity prior', 'Each token is weighted by how rare it is in general English (wordfreq Zipf scale). Filler like “the” weighs zero; coined identifiers score highest. Self-maintaining — no stopword list to curate.'],
      ['Repo-adaptive multiplier', 'Words rare in English but ubiquitous in your repo’s own routed prompts (“memory”, “topics”) are shrunk by a document-frequency factor over the query log, cached in query_df.json.'],
      ['Per-graph saturation filter', 'A keyword present in over a third of all topics can’t distinguish between them and is dropped before scoring — however rare it is in English.'],
    ], '480px', []),
    P('The winner still needs at least two distinct informative keywords, and identity-text hits (label, intent, aliases) always outrank ref-path hits. And every route has a basis you can interrogate — the dashboard\'s route playground shows which strategy fired and, for fuzzy routes, the exact keywords behind it — so a surprising route can be explained instead of shrugged at.'),
  ]],
  ['What the agent receives', [
    C('$ regin topics route "span ingest"\n{\n  "status": "approved",\n  "topic":  { "id": "session-trace-design", ... },\n  "refs":   [ ...ordered by role: overview → entrypoint → schema → tests ],\n  "wiki_pages": [ { "path": ".regin/topics/wiki/session-trace-design.md", ... } ],\n  "related":    [ { "id": "trace-span-capture", ... } ]\n}'),
    P('Refs come back ordered by role — overview and architecture before entrypoints, entrypoints before implementation — so an agent reads the way you\'d onboard a new teammate. Wiki pages arrive inline under a character budget, flagged when truncated. Related topics come with their own refs and wiki paths, one deliberate hop away.'),
  ]],
  ['The topic-router skill', [
    P('The router is only useful if the agent actually calls it, so regin generates a `topic-router` skill and deploys it with the rest. It teaches the discipline: distill the task into 2–6 stable keywords (nouns, file names, feature names — not the whole sentence), route them, read wiki first and refs in role order, and retry once with narrower keywords when a route looks weak.'),
    N('TIP', 'Routing only ever serves the **approved** graph. Draft proposals are never routed; promoting one is an explicit review step, not a side effect of an agent finding it useful.'),
  ]],
  ['CLI', [
    T(COMMAND, COMMAND_GRID, [
      ['regin topics route "<keywords>"', 'Print the full route envelope as JSON (status, topic, refs, wiki, related).'],
      ['regin topics route "<keywords>" --wiki', 'Print the routed topic’s wiki markdown content-first — built for agents piping through head.'],
      ['regin topics router-skill', 'Print the generated topic-router skill that teaches an agent the keyword discipline.'],
      ['regin topics rebuild-query-df', 'Recompute the repo-adaptive term-frequency cache from the routed-prompt corpus.'],
      ['regin topics scan', 'Refresh approved topics’ refs from the current working tree — the inputs the matcher reads.'],
    ], '500px'),
    P('Next: where topics come from — the [proposal pipeline](/topics/proposals).'),
  ]],
]

export const PROPOSALS = [
  ['The funnel', [
    C('topic request ("map the trace subsystem")\n   ↓\nexternal agent explores the repo        # its own Read / Glob / Grep —\n   ↓                                    # no pre-baked evidence pack\ndraft topics + one wiki page each\n   ↓  regin topics proposal-finish      # the agent signals completion itself\nvalidated & persisted → pending_review\n   ↓\nreview: comment · regenerate · restore\n   ↓\n/diff → /apply, one topic at a time → .regin/topics/topic.json'),
    P('Everything in this pipeline writes *proposal* artifacts. The approved graph is touched by exactly one path — apply, at the end, after review — and the runner fingerprints the graph\'s structure to reject any draft run that mutated it.'),
  ]],
  ['Drafting', [
    P('The drafting agent is a real tool-using agent (your configured `claude` or `codex`) exploring the repo with its own tools. regin hands it the existing topic and bucket lists — so it extends the graph instead of duplicating it — plus your request and the finish command; everything else it learns from the code itself. Each proposed topic carries its own wiki page; a draft with no wiki fails validation, as does any ref path that doesn\'t exist in the working tree.'),
    P('Long drafts don\'t race a timeout: the agent signals completion itself by running `regin topics proposal-finish` as its final step, which ingests the output in the agent\'s own process and links the run to its real session trace. A run can be stopped mid-flight, and runs stranded by a server restart are reaped as failed instead of hanging forever.'),
  ]],
  ['Review iteration', [
    P('A finished draft lands as an inbox card and waits in `pending_review`. Review is iterative, not accept/reject:'),
    T(['ACTION', 'WHAT IT DOES'], 'minmax(170px,1fr) minmax(260px,2.2fr)', [
      ['Feedback threads', 'Comments anchored to a topic, a summary, or a wiki range; open threads are replayed into the next redraft prompt.'],
      ['Regenerate', 'Re-run the drafting agent with the prior draft and all open feedback as context.'],
      ['Scoped regenerate', 'Redraft only the topics whose refs drifted; every other wiki page stays byte-identical — enforced by a server-side splice, not agent obedience.'],
      ['Restore', 'Copy a historical revision forward when a redraft made things worse.'],
      ['Agentic review note', 'A reviewer agent opens the drafted refs as they exist now and files an ACCEPT / REGENERATE / DISMISS recommendation as an ordinary thread.'],
    ], '480px', []),
    N('TIP', 'The review surface is shared by humans and agents: an agentic review note is an ordinary feedback thread, so it shows in the same sidebar and rides into the next redraft alongside your comments.'),
  ]],
  ['Two status axes', [
    P('A proposal run tracks two independent things — whether the *process* finished and whether the *content* is approved — and keeping them on separate axes is what prevents a background job from silently un-approving a reviewed draft:'),
    T(['AXIS', 'STATES', 'OWNED BY'], '130px minmax(220px,1.6fr) minmax(180px,1fr)', [
      ['Run lifecycle', 'queued → running → completed | failed | cancelled | timed_out | waiting_for_permission', 'The runner, background job, stop, and reap paths.'],
      ['Review state', 'draft → pending_review → changes_requested → ready_to_apply → partially_applied → applied', 'The save and apply paths only — a run-state write can never clobber it.'],
    ], '560px', [1]),
  ]],
  ['Applying', [
    P('Apply is server-side and per-topic. The diff you preview is recomputed at apply time from the same inputs — the client never submits a diff, so a stale preview can\'t commit a stale graph. Apply refuses to run unless the proposal is marked ready, reports anything the chosen options would silently drop (orphan edges, dead refs, duplicate aliases), and an operation can never introduce *new* graph errors: pre-existing rot elsewhere becomes a warning, not a hard block.'),
    P('There\'s a reverse gear, too: downgrading lifts an approved topic back into its origin proposal for re-drafting, pruned edges recorded so a later apply can restore them.'),
    P('Next: how approved pages stay truthful — [Drift & Evolution](/topics/evolution).'),
  ]],
]

export const EVOLUTION = [
  ['Why wikis rot', [
    P('Every topic names the files it explains. Those files keep changing after the wiki is approved: functions move, modules get renamed, a refactor hollows out the mechanism the page describes. The wiki doesn\'t know. Drift detection makes the staleness *visible* — by fingerprinting each topic\'s refs when a page is approved and comparing on demand — so freshness stops depending on someone remembering.'),
  ]],
  ['The mechanisms', [
    T(COMMAND, COMMAND_GRID, [
      ['regin topics digest-refs', 'Fingerprint each topic’s reference files — the drift baseline.'],
      ['regin topics evolve', 'Detect content drift against the baseline and emit refresh proposals. Gated by evolution_enabled.'],
      ['regin topics wiki-debt', 'Report topics with a missing or drifted wiki, optionally scoped to a git diff — the close-out check after a change lands.'],
      ['regin topics drift', 'Follow git renames into topic refs and memory paths. Writes only with mechanical_autoapply.'],
      ['regin topics cascade-stale', 'Cascade a stale topic to its linked memories: their veracity drops to unknown until re-verified.'],
      ['regin topics wiki-stats', 'Per-wiki recall counts — exposure with no reads is a prune or refresh signal.'],
      ['regin topics backfill-tiers', 'Tag refs the wiki never mentions as tier=reference so they stop emitting drift debt. Dry-run by default.'],
    ], '500px'),
    P('`wiki-debt` is the one to wire into your workflow: scoped to a diff (`--changed-since <base>`) it answers, in under a second, "did my change strand any topic\'s wiki?" — the natural close-out check after a feature lands.'),
  ]],
  ['Refresh proposals', [
    P('Detection never edits a page. A drifted topic gets a *refresh proposal* — the same reviewable draft as any new topic, through the same pipeline. The redraft is scoped: only the drifted topics are re-derived from the code, and a server-side splice keeps every untouched wiki page byte-identical regardless of what the drafting agent returns. A topic that drifted badly can also cascade to its [linked memories](/topics/memory), dropping their veracity to unknown until re-verified.'),
    N('WARN', '**Everything on this page defaults to off.** Detection commands always run read-only; the write paths — auto-applying renames, auto-spawning refresh agents — each sit behind their own flag, and even then a draft only ever lands as `pending_review`. Nothing self-applies to the approved graph.'),
  ]],
  ['Settings', [
    P('The `topic_evolution` block in [Configuration](/configuration#s-other-blocks):'),
    T(['KEY', 'DEFAULT', 'EFFECT'], '200px 80px minmax(220px,1fr)', [
      ['evolution_enabled', 'false', 'Unlock the drift/cascade machinery.'],
      ['mechanical_autoapply', 'false', 'Let ref renames write to the local overlay without review (never the approved topic.json).'],
      ['auto_spawn_agents', 'false', 'Launch the external drafting agent for refresh proposals automatically.'],
    ], '560px', [0, 1]),
    P('Next: how the graph and cross-session memory reinforce each other — [Topics × Memory](/topics/memory).'),
  ]],
]

export const MEMORY = [
  ['Lessons file themselves', [
    P('regin\'s [agent memory](/architecture#s-the-guidance-layer) stores what sessions learned; the topic graph stores how the repo is organized. Linking them gives each half what it lacks — memories get a browsable structure, topics get living experience attached to the code they describe.'),
    P('When a session records a lesson — a gotcha, a root cause, a decision and its why — regin classifies it under a node of the topic taxonomy. The graph you review for code knowledge doubles as the index of everything past sessions learned in that area: browse a topic and its hard-won lessons sit next to its wiki and refs. Unfiled memories are a visible curation queue, not a silent leak.'),
  ]],
  ['Tree navigation', [
    P('Keyword recall answers "find me memories like this query." Tree navigation answers a different question — "what does this repo know about *that subsystem*?" — with three read-only tools an agent walks coarse-to-fine:'),
    C('index_root()               → the top-level buckets, each with a\n                             one-line blurb + memory count\nindex_expand("webui")      → that bucket\'s children, as cards —\n                             prune subtrees by reading blurbs\nindex_fetch("webui-styling")\n  ## wiki                  → the topic\'s wiki path\n  ## source refs           → files by role\n  ## memories              → lesson titles + ids, importance-ranked\n                             (addresses only — open what you choose)'),
    P('Every step returns addresses, not bodies, so a heavily-documented topic can\'t flood the agent\'s context; it opens only the wiki or the two lessons that look on-point. A bucket with zero memories is an honest answer too: a real knowledge gap, not a failed search.'),
  ]],
  ['Routes that learn', [
    P('Every topic banner injected into a session is recorded, and the loop closes on whether it actually helped:'),
    T(['SIGNAL', 'WHAT IT DRIVES'], 'minmax(160px,1fr) minmax(260px,2.4fr)', [
      ['Relevance verdict', 'The session grader judges every injected topic banner against what the session actually did: satisfied, needs_revision, or fail.'],
      ['Recurring failure', 'A topic whose banners keep failing is proposed for suppression — withholding is a human decision, never automatic.'],
      ['Signed exemplars', 'Each verdict also stores the prompt’s embedding as a ±1 exemplar, so one topic can be suppressed for queries like the ones it failed on while still routing for the rest.'],
    ], '480px', []),
    P('The Memory view in the dashboard surfaces the whole loop — per-topic verdict summaries, the judge log, and a route playground for probing what any query would resolve to before an agent ever runs.'),
    P('That closes the section. Back to the [overview](/topics), or see how the pieces fit in [Architecture](/architecture).'),
  ]],
]
