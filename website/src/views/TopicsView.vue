<script setup>
import DocPage from '../components/DocPage.vue'
import CodeBlock from '../components/CodeBlock.vue'
import TopicsSubnav from '../components/TopicsSubnav.vue'
import SiteIcon from '../components/SiteIcon.vue'
import { SUBPAGE_SUMMARIES } from '../content/topics.js'

const TOC = [
  { id: 'anatomy', label: 'What a topic is' },
  { id: 'files', label: 'Where the graph lives' },
  { id: 'lifecycle', label: 'The lifecycle' },
  { id: 'section-pages', label: 'In this section' },
]

const TOPIC_JSON = `{
  "id": "session-trace-design",
  "label": "Session trace design",
  "aliases": ["trace spans", "span ingest"],
  "intent": "How sessions become queryable spans",
  "refs": [
    { "path": "lib/trace/ingest.py", "role": "entrypoint" },
    { "path": "lib/trace/merge.py",  "role": "implementation" },
    { "path": "db/schema.sql",       "role": "schema" }
  ],
  "edges": [{ "target": "trace-span-capture", "type": "related" }]
}
# + .regin/topics/wiki/session-trace-design.md — the narrative page`
</script>

<template>
  <DocPage
    title="Topic Wikis"
    lead="Long-lived knowledge about your repo, structured as a reviewable graph of topics — each with the files that matter, a narrative wiki page, and links to related topics — routed to the agent only when a task touches it."
    :toc="TOC"
  >
    <template #subnav><TopicsSubnav /></template>

    <p>A coding agent starts every session knowing nothing about your repo beyond what fits in its prompt. Dumping documentation at it wastes context on the 95% that doesn't apply; writing nothing forces it to rediscover your architecture — sometimes wrongly — every single time. Topic wikis are the middle path: knowledge is broken into <em>topics</em> sized to a task, and the agent receives only the one or two slices touching what it's doing right now.</p>

    <h2 id="anatomy">What a topic is</h2>
    <p>A topic is a named slice of the repo with everything an agent needs to work in that area:</p>
    <CodeBlock :code="TOPIC_JSON" />
    <ul>
      <li><strong>Refs with roles</strong> — the files that matter, ordered by how a newcomer should read them: overview and architecture first, then entrypoints, APIs, schemas, implementation, tests.</li>
      <li><strong>A wiki page</strong> — the narrative: how the pieces fit, the invariants, the gotchas. Written against the code as it exists, and checked against it later (see <RouterLink to="/topics/evolution">Drift &amp; evolution</RouterLink>).</li>
      <li><strong>Aliases and intent</strong> — the vocabulary the <RouterLink to="/topics/routing">router</RouterLink> matches a task's keywords against.</li>
      <li><strong>Edges</strong> — first-degree links to related topics, so an agent can widen its context one deliberate hop at a time.</li>
    </ul>

    <h2 id="files">Where the graph lives</h2>
    <p>Everything is plain files inside the repo, so knowledge travels the same way code does — through git:</p>
    <ul>
      <li><code>.regin/topics/topic.json</code> — the approved graph, git-tracked and shared with your team. Nothing writes to it without review.</li>
      <li><code>.regin/topics/topic.local.json</code> — a machine-local overlay for topics you haven't promoted yet.</li>
      <li><code>.regin/topics/wiki/&lt;id&gt;.md</code> — one wiki page per topic, plus a generated <code>index.md</code>.</li>
    </ul>
    <p>Commit the graph and a teammate's clone routes to the same knowledge; <code>regin topics install-hook</code> adds git hooks that keep each machine's snapshot in sync after pulls and merges.</p>

    <h2 id="lifecycle">The lifecycle</h2>
    <p>Topics are agent-drafted but human-approved, and they don't get to rot quietly:</p>
    <ol>
      <li>An external agent explores the repo and <RouterLink to="/topics/proposals">proposes</RouterLink> draft topics; you review, iterate, and apply them one at a time.</li>
      <li>At task time, the <RouterLink to="/topics/routing">router</RouterLink> resolves the agent's keywords to one approved topic and hands over its refs, wiki, and neighbors.</li>
      <li>As the code moves on, <RouterLink to="/topics/evolution">drift detection</RouterLink> flags pages whose underlying files changed, and refresh proposals bring them back in line.</li>
      <li>Cross-session <RouterLink to="/topics/memory">memory lessons</RouterLink> file themselves under topic nodes, so the graph doubles as an index of everything past sessions learned.</li>
    </ol>

    <h2 id="section-pages">In this section</h2>
    <ul class="mini-list">
      <li v-for="page in SUBPAGE_SUMMARIES" :key="page.to">
        <RouterLink :to="page.to" class="mini-row focus-visible:ring">
          <span class="mini-title">{{ page.label }}</span>
          <span class="mini-body">{{ page.summary }}</span>
          <SiteIcon name="arrow-right" :size="16" />
        </RouterLink>
      </li>
    </ul>
  </DocPage>
</template>
