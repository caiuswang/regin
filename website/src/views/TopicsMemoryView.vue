<script setup>
import DocPage from '../components/DocPage.vue'
import CodeBlock from '../components/CodeBlock.vue'
import DataTable from '../components/DataTable.vue'
import TopicsSubnav from '../components/TopicsSubnav.vue'
import { MEMORY_SIGNALS } from '../content/topics.js'

const TOC = [
  { id: 'filing', label: 'Lessons file themselves' },
  { id: 'treenav', label: 'Tree navigation' },
  { id: 'feedback', label: 'Routes that learn' },
]

const SIGNAL_COLUMNS = [
  { key: 'signal', label: 'Signal' },
  { key: 'effect', label: 'What it drives' },
]

const TREE_WALK = `index_root()               → the top-level buckets, each with a
                             one-line blurb + memory count
index_expand("webui")      → that bucket's children, as cards —
                             prune subtrees by reading blurbs
index_fetch("webui-styling")
  ## wiki                  → the topic's wiki path
  ## source refs           → files by role
  ## memories              → lesson titles + ids, importance-ranked
                             (addresses only — open what you choose)`
</script>

<template>
  <DocPage
    title="Topics &times; Memory"
    lead="The topic graph and cross-session memory are one system: lessons file themselves under topics, agents recall by walking the tree instead of guessing keywords, and bad routes feed back into better ones."
    :toc="TOC"
  >
    <template #subnav><TopicsSubnav /></template>

    <p>regin's <RouterLink to="/architecture#memory">agent memory</RouterLink> stores what sessions learned; the topic graph stores how the repo is organized. Linking them gives each half what it lacks — memories get a browsable structure, topics get living experience attached to the code they describe.</p>

    <h2 id="filing">Lessons file themselves</h2>
    <p>When a session records a lesson — a gotcha, a root cause, a decision and its why — regin classifies it under a node of the topic taxonomy. The graph you review for code knowledge doubles as the index of everything past sessions learned in that area: browse a topic and its hard-won lessons sit next to its wiki and refs. Unfiled memories are a visible curation queue, not a silent leak.</p>

    <h2 id="treenav">Tree navigation</h2>
    <p>Keyword recall answers "find me memories like this query." Tree navigation answers a different question — "what does this repo know about <em>that subsystem</em>?" — with three read-only tools an agent walks coarse-to-fine:</p>
    <CodeBlock :code="TREE_WALK" />
    <p>Every step returns addresses, not bodies, so a heavily-documented topic can't flood the agent's context; it opens only the wiki or the two lessons that look on-point. A bucket with zero memories is an honest answer too: a real knowledge gap, not a failed search.</p>

    <h2 id="feedback">Routes that learn</h2>
    <p>Every topic banner injected into a session is recorded, and the loop closes on whether it actually helped:</p>
    <DataTable :columns="SIGNAL_COLUMNS" :rows="MEMORY_SIGNALS" />
    <p>The Memory view in the dashboard surfaces the whole loop — per-topic verdict summaries, the judge log, and a route playground for probing what any query would resolve to before an agent ever runs.</p>
    <p>That closes the section. Back to the <RouterLink to="/topics">overview</RouterLink>, or see how the pieces fit in <RouterLink to="/architecture">Architecture</RouterLink>.</p>
  </DocPage>
</template>
