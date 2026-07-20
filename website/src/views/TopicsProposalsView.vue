<script setup>
import DocPage from '../components/DocPage.vue'
import CodeBlock from '../components/CodeBlock.vue'
import Callout from '../components/Callout.vue'
import DataTable from '../components/DataTable.vue'
import TopicsSubnav from '../components/TopicsSubnav.vue'
import { STATUS_AXES, REVIEW_ACTIONS } from '../content/topics.js'

const TOC = [
  { id: 'funnel', label: 'The funnel' },
  { id: 'drafting', label: 'Drafting' },
  { id: 'review', label: 'Review iteration' },
  { id: 'axes', label: 'Two status axes' },
  { id: 'apply', label: 'Applying' },
]

const AXIS_COLUMNS = [
  { key: 'axis', label: 'Axis' },
  { key: 'states', label: 'States', code: true },
  { key: 'owner', label: 'Owned by' },
]
const ACTION_COLUMNS = [
  { key: 'action', label: 'Action' },
  { key: 'desc', label: 'What it does' },
]

const FUNNEL = `topic request ("map the trace subsystem")
   ↓
external agent explores the repo        # its own Read / Glob / Grep —
   ↓                                    # no pre-baked evidence pack
draft topics + one wiki page each
   ↓  regin topics proposal-finish      # the agent signals completion itself
validated & persisted → pending_review
   ↓
review: comment · regenerate · restore
   ↓
/diff → /apply, one topic at a time → .regin/topics/topic.json`
</script>

<template>
  <DocPage
    title="Topic Proposals"
    lead="Topics are drafted by an agent that reads your real code, and applied only after a human review — the pipeline in between is built so nothing self-promotes into the approved graph."
    :toc="TOC"
  >
    <template #subnav><TopicsSubnav /></template>

    <h2 id="funnel">The funnel</h2>
    <CodeBlock :code="FUNNEL" />
    <p>Everything in this pipeline writes <em>proposal</em> artifacts. The approved graph is touched by exactly one path — apply, at the end, after review — and the runner fingerprints the graph's structure to reject any draft run that mutated it.</p>

    <h2 id="drafting">Drafting</h2>
    <p>The drafting agent is a real tool-using agent (your configured <code>claude</code> or <code>codex</code>) exploring the repo with its own tools. regin hands it the existing topic and bucket lists — so it extends the graph instead of duplicating it — plus your request and the finish command; everything else it learns from the code itself. Each proposed topic carries its own wiki page; a draft with no wiki fails validation, as does any ref path that doesn't exist in the working tree.</p>
    <p>Long drafts don't race a timeout: the agent signals completion itself by running <code>regin topics proposal-finish</code> as its final step, which ingests the output in the agent's own process and links the run to its real session trace. A run can be stopped mid-flight, and runs stranded by a server restart are reaped as failed instead of hanging forever.</p>

    <h2 id="review">Review iteration</h2>
    <p>A finished draft lands as an inbox card and waits in <code>pending_review</code>. Review is iterative, not accept/reject:</p>
    <DataTable :columns="ACTION_COLUMNS" :rows="REVIEW_ACTIONS" />
    <Callout tone="info">
      The review surface is shared by humans and agents: an agentic review note is an ordinary
      feedback thread, so it shows in the same sidebar and rides into the next redraft
      alongside your comments.
    </Callout>

    <h2 id="axes">Two status axes</h2>
    <p>A proposal run tracks two independent things — whether the <em>process</em> finished and whether the <em>content</em> is approved — and keeping them on separate axes is what prevents a background job from silently un-approving a reviewed draft:</p>
    <DataTable :columns="AXIS_COLUMNS" :rows="STATUS_AXES" />

    <h2 id="apply">Applying</h2>
    <p>Apply is server-side and per-topic. The diff you preview is recomputed at apply time from the same inputs — the client never submits a diff, so a stale preview can't commit a stale graph. Apply refuses to run unless the proposal is marked ready, reports anything the chosen options would silently drop (orphan edges, dead refs, duplicate aliases), and an operation can never introduce <em>new</em> graph errors: pre-existing rot elsewhere becomes a warning, not a hard block.</p>
    <p>There's a reverse gear, too: downgrading lifts an approved topic back into its origin proposal for re-drafting, pruned edges recorded so a later apply can restore them.</p>
    <p>Next: how approved pages stay truthful — <RouterLink to="/topics/evolution">Drift &amp; evolution</RouterLink>.</p>
  </DocPage>
</template>
