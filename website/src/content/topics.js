export const SECTION_PAGES = [
  { to: '/topics', label: 'Overview' },
  {
    to: '/topics/routing',
    label: 'Routing',
    summary: 'How 2–6 keywords resolve to one approved topic — and why a vague query honestly resolves to nothing.',
  },
  {
    to: '/topics/proposals',
    label: 'Proposals & review',
    summary: 'An agent drafts topics against your real code; you review, iterate, and apply one topic at a time.',
  },
  {
    to: '/topics/evolution',
    label: 'Drift & evolution',
    summary: 'Content-drift detection, wiki debt, and the (default-off) machinery that refreshes stale pages.',
  },
  {
    to: '/topics/memory',
    label: 'Memory links',
    summary: 'The same tree files cross-session lessons, powers coarse-to-fine recall, and learns from bad routes.',
  },
]

export const SUBPAGE_SUMMARIES = SECTION_PAGES.filter((page) => page.summary)

export const MATCH_STRATEGIES = [
  { n: '1', strategy: 'Alias / label — exact', fires: 'The whole query equals a topic id, label, or alias.' },
  { n: '2', strategy: 'Ref path — exact', fires: 'The query equals one of a topic’s reference file paths.' },
  { n: '3', strategy: 'Ref path — substring', fires: 'The query appears inside a reference path (`merge.py`, `blueprints/topics`).' },
  { n: '4', strategy: 'Label / intent — substring', fires: 'The query appears in the topic’s id + label + intent + aliases text.' },
  { n: '5', strategy: 'Fuzzy multi-keyword', fires: 'Fallback for prose: at least 2 distinct informative keywords must land on the same topic.' },
]

export const SIGNAL_LAYERS = [
  {
    layer: 'English-rarity prior',
    what: 'Each token is weighted by how rare it is in general English (wordfreq Zipf scale). Filler like “the” weighs zero; coined identifiers score highest. Self-maintaining — no stopword list to curate.',
  },
  {
    layer: 'Repo-adaptive multiplier',
    what: 'Words rare in English but ubiquitous in your repo’s own routed prompts (“memory”, “topics”) are shrunk by a document-frequency factor over the query log, cached in query_df.json.',
  },
  {
    layer: 'Per-graph saturation filter',
    what: 'A keyword present in over a third of all topics can’t distinguish between them and is dropped before scoring — however rare it is in English.',
  },
]

export const ROUTING_CLI = [
  { cmd: 'regin topics route "<keywords>"', desc: 'Print the full route envelope as JSON (status, topic, refs, wiki, related).' },
  { cmd: 'regin topics route "<keywords>" --wiki', desc: 'Print the routed topic’s wiki markdown content-first — built for agents piping through head.' },
  { cmd: 'regin topics router-skill', desc: 'Print the generated topic-router skill that teaches an agent the keyword discipline.' },
  { cmd: 'regin topics rebuild-query-df', desc: 'Recompute the repo-adaptive term-frequency cache from the routed-prompt corpus.' },
  { cmd: 'regin topics scan', desc: 'Refresh approved topics’ refs from the current working tree — the inputs the matcher reads.' },
]

export const STATUS_AXES = [
  {
    axis: 'Run lifecycle',
    states: 'queued → running → completed | failed | cancelled | timed_out | waiting_for_permission',
    owner: 'The runner, background job, stop, and reap paths.',
  },
  {
    axis: 'Review state',
    states: 'draft → pending_review → changes_requested → ready_to_apply → partially_applied → applied',
    owner: 'The save and apply paths only — a run-state write can never clobber it.',
  },
]

export const REVIEW_ACTIONS = [
  { action: 'Feedback threads', desc: 'Comments anchored to a topic, a summary, or a wiki range; open threads are replayed into the next redraft prompt.' },
  { action: 'Regenerate', desc: 'Re-run the drafting agent with the prior draft and all open feedback as context.' },
  { action: 'Scoped regenerate', desc: 'Redraft only the topics whose refs drifted; every other wiki page stays byte-identical — enforced by a server-side splice, not agent obedience.' },
  { action: 'Restore', desc: 'Copy a historical revision forward when a redraft made things worse.' },
  { action: 'Agentic review note', desc: 'A reviewer agent opens the drafted refs as they exist now and files an ACCEPT / REGENERATE / DISMISS recommendation as an ordinary thread.' },
]

export const EVOLUTION_MECHANISMS = [
  { cmd: 'regin topics digest-refs', desc: 'Fingerprint each topic’s reference files — the drift baseline.' },
  { cmd: 'regin topics evolve', desc: 'Detect content drift against the baseline and emit refresh proposals. Gated by evolution_enabled.' },
  { cmd: 'regin topics wiki-debt', desc: 'Report topics with a missing or drifted wiki, optionally scoped to a git diff — the close-out check after a change lands.' },
  { cmd: 'regin topics drift', desc: 'Follow git renames into topic refs and memory paths. Writes only with mechanical_autoapply.' },
  { cmd: 'regin topics cascade-stale', desc: 'Cascade a stale topic to its linked memories: their veracity drops to unknown until re-verified.' },
  { cmd: 'regin topics wiki-stats', desc: 'Per-wiki recall counts — exposure with no reads is a prune or refresh signal.' },
  { cmd: 'regin topics backfill-tiers', desc: 'Tag refs the wiki never mentions as tier=reference so they stop emitting drift debt. Dry-run by default.' },
]

export const MEMORY_SIGNALS = [
  { signal: 'Relevance verdict', effect: 'The session grader judges every injected topic banner against what the session actually did: satisfied, needs_revision, or fail.' },
  { signal: 'Recurring failure', effect: 'A topic whose banners keep failing is proposed for suppression — withholding is a human decision, never automatic.' },
  { signal: 'Signed exemplars', effect: 'Each verdict also stores the prompt’s embedding as a ±1 exemplar, so one topic can be suppressed for queries like the ones it failed on while still routing for the rest.' },
]
