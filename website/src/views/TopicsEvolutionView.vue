<script setup>
import DocPage from '../components/DocPage.vue'
import Callout from '../components/Callout.vue'
import DataTable from '../components/DataTable.vue'
import TopicsSubnav from '../components/TopicsSubnav.vue'
import { EVOLUTION_MECHANISMS } from '../content/topics.js'
import { NESTED_BLOCKS } from '../content/settings.js'
import { COMMAND_COLUMNS, SETTING_COLUMNS } from '../content/columns.js'

const EVOLUTION_SETTINGS = NESTED_BLOCKS.find((block) => block.id === 'topic-evolution').rows

const TOC = [
  { id: 'why', label: 'Why wikis rot' },
  { id: 'mechanisms', label: 'The mechanisms' },
  { id: 'refresh', label: 'Refresh proposals' },
  { id: 'settings', label: 'Settings' },
]

</script>

<template>
  <DocPage
    title="Drift &amp; Evolution"
    lead="Documentation that was true in March and is wrong in July is worse than no documentation — an agent will trust it. regin detects when code moves out from under a wiki, and routes the fix through the same review pipeline that created the page."
    :toc="TOC"
  >
    <template #subnav><TopicsSubnav /></template>

    <h2 id="why">Why wikis rot</h2>
    <p>Every topic names the files it explains. Those files keep changing after the wiki is approved: functions move, modules get renamed, a refactor hollows out the mechanism the page describes. The wiki doesn't know. Drift detection makes the staleness <em>visible</em> — by fingerprinting each topic's refs when a page is approved and comparing on demand — so freshness stops depending on someone remembering.</p>

    <h2 id="mechanisms">The mechanisms</h2>
    <DataTable :columns="COMMAND_COLUMNS" :rows="EVOLUTION_MECHANISMS" />
    <p><code>wiki-debt</code> is the one to wire into your workflow: scoped to a diff (<code>--changed-since &lt;base&gt;</code>) it answers, in under a second, "did my change strand any topic's wiki?" — the natural close-out check after a feature lands.</p>

    <h2 id="refresh">Refresh proposals</h2>
    <p>Detection never edits a page. A drifted topic gets a <em>refresh proposal</em> — the same reviewable draft as any new topic, through the same pipeline. The redraft is scoped: only the drifted topics are re-derived from the code, and a server-side splice keeps every untouched wiki page byte-identical regardless of what the drafting agent returns. A topic that drifted badly can also cascade to its <RouterLink to="/topics/memory">linked memories</RouterLink>, dropping their veracity to unknown until re-verified.</p>
    <Callout tone="warn">
      <strong>Everything on this page defaults to off.</strong> Detection commands always run
      read-only; the write paths — auto-applying renames, auto-spawning refresh agents — each
      sit behind their own flag, and even then a draft only ever lands as
      <code>pending_review</code>. Nothing self-applies to the approved graph.
    </Callout>

    <h2 id="settings">Settings</h2>
    <p>The <code>topic_evolution</code> block in <RouterLink to="/configuration#topic-evolution">Configuration</RouterLink>:</p>
    <DataTable :columns="SETTING_COLUMNS" :rows="EVOLUTION_SETTINGS" />
    <p>Next: how the graph and cross-session memory reinforce each other — <RouterLink to="/topics/memory">Memory links</RouterLink>.</p>
  </DocPage>
</template>
