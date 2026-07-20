<script setup>
import DocPage from '../components/DocPage.vue'
import CodeBlock from '../components/CodeBlock.vue'
import Callout from '../components/Callout.vue'
import DataTable from '../components/DataTable.vue'
import TopicsSubnav from '../components/TopicsSubnav.vue'
import { MATCH_STRATEGIES, SIGNAL_LAYERS, ROUTING_CLI } from '../content/topics.js'
import { COMMAND_COLUMNS } from '../content/columns.js'

const TOC = [
  { id: 'matcher', label: 'The matcher' },
  { id: 'signal', label: 'Keyword weighting' },
  { id: 'envelope', label: 'What the agent receives' },
  { id: 'skill', label: 'The topic-router skill' },
  { id: 'cli', label: 'CLI' },
]

const STRATEGY_COLUMNS = [
  { key: 'n', label: '#' },
  { key: 'strategy', label: 'Strategy' },
  { key: 'fires', label: 'Matches when' },
]
const SIGNAL_COLUMNS = [
  { key: 'layer', label: 'Layer' },
  { key: 'what', label: 'What it corrects for' },
]
const ENVELOPE = `$ regin topics route "span ingest"
{
  "status": "approved",
  "topic":  { "id": "session-trace-design", ... },
  "refs":   [ ...ordered by role: overview → entrypoint → schema → tests ],
  "wiki_pages": [ { "path": ".regin/topics/wiki/session-trace-design.md", ... } ],
  "related":    [ { "id": "trace-span-capture", ... } ]
}`
</script>

<template>
  <DocPage
    title="Topic Routing"
    lead="How a handful of task keywords resolves to one approved topic — deterministically, with no model call — and hands the agent exactly the refs, wiki, and neighbors it needs."
    :toc="TOC"
  >
    <template #subnav><TopicsSubnav /></template>

    <p>Routing is the read path of the <RouterLink to="/topics">topic graph</RouterLink>. It is a keyword router, not a semantic search: given a query it either finds a topic it can <em>explain</em> matching — down to the exact keywords that fired — or it honestly returns nothing. No embeddings, no inference cost, no coincidental matches dressed up as relevance.</p>

    <h2 id="matcher">The matcher</h2>
    <p>Five strategies run in priority order; the first hit wins. The four precise strategies each scan every topic before the fuzzy fallback gets a turn, so an exact alias match on one topic always beats a loose substring match on another.</p>
    <DataTable :columns="STRATEGY_COLUMNS" :rows="MATCH_STRATEGIES" />
    <p>A miss returns <code>status: "unmatched"</code> with empty refs. That's deliberate: for prose with no topical vocabulary, no context is more useful than the wrong context.</p>

    <h2 id="signal">Keyword weighting</h2>
    <p>The fuzzy fallback scores keyword overlap by <em>informativeness</em>, not raw hit count — one rare domain term outweighs three common words landing by coincidence. Three layers stack:</p>
    <DataTable :columns="SIGNAL_COLUMNS" :rows="SIGNAL_LAYERS" />
    <p>The winner still needs at least two distinct informative keywords, and identity-text hits (label, intent, aliases) always outrank ref-path hits. And every route has a basis you can interrogate — the dashboard's route playground shows which strategy fired and, for fuzzy routes, the exact keywords behind it — so a surprising route can be explained instead of shrugged at.</p>

    <h2 id="envelope">What the agent receives</h2>
    <CodeBlock :code="ENVELOPE" />
    <p>Refs come back ordered by role — overview and architecture before entrypoints, entrypoints before implementation — so an agent reads the way you'd onboard a new teammate. Wiki pages arrive inline under a character budget, flagged when truncated. Related topics come with their own refs and wiki paths, one deliberate hop away.</p>

    <h2 id="skill">The topic-router skill</h2>
    <p>The router is only useful if the agent actually calls it, so regin generates a <code>topic-router</code> skill and deploys it with the rest. It teaches the discipline: distill the task into 2–6 stable keywords (nouns, file names, feature names — not the whole sentence), route them, read wiki first and refs in role order, and retry once with narrower keywords when a route looks weak.</p>
    <Callout tone="info">
      Routing only ever serves the <strong>approved</strong> graph. Draft proposals are never routed;
      promoting one is an explicit review step, not a side effect of an agent finding it useful.
    </Callout>

    <h2 id="cli">CLI</h2>
    <DataTable :columns="COMMAND_COLUMNS" :rows="ROUTING_CLI" />
    <p>Next: where topics come from — the <RouterLink to="/topics/proposals">proposal pipeline</RouterLink>.</p>
  </DocPage>
</template>
