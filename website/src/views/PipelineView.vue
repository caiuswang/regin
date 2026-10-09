<script setup>
import DocPage from '../components/DocPage.vue'
import CodeBlock from '../components/CodeBlock.vue'
import DataTable from '../components/DataTable.vue'
import BlockShot from '../components/blocks/BlockShot.vue'
import defectsShot from '../assets/shots/pixel/defects.webp'
import featuresShot from '../assets/shots/pixel/features.webp'
import graphShot from '../assets/shots/pixel/taskgraph.webp'
import overviewShot from '../assets/shots/pixel/overview.webp'
import Callout from '../components/Callout.vue'
import {
  LOOP_STEPS, LOOP_COLUMNS, PIXEL_SLICES, SLICE_COLUMNS, PIXEL_FACTS, FACT_COLUMNS,
  LEDGER, SURFACES, SURFACE_COLUMNS,
} from '../content/pipeline.js'


const TOC = [
  { id: 'loop', label: 'The loop' },
  { id: 'example', label: 'Worked example' },
  { id: 'spec', label: 'Spec' },
  { id: 'slices', label: 'Slices → task graph' },
  { id: 'runs', label: 'Runs' },
  { id: 'defects', label: 'Defects' },
  { id: 'ship', label: 'Ship and count' },
  { id: 'features', label: 'Features tab' },
  { id: 'where', label: 'Where to find it' },
]
</script>

<template>
  <DocPage
    title="Pipeline"
    lead="The Workbench layer, start to finish: a feature carries its spec and acceptance tests, is cut into tasks that run in dependency order, is held to a done-claim, and is charged for every defect found against it. This page follows one real feature through it — the Pixel theme the screenshots below are wearing."
    :toc="TOC"
  >
    <h2 id="loop">The loop</h2>
    <p>The dashboard’s <strong>Pipeline</strong> has three tabs — Overview, Features, Tasks — and a project picker that scopes all of them. The Overview draws the whole loop for one project: features in the workshop, tasks on the board, defects in the bug den, and what is waiting on you.</p>
    <BlockShot :src="overviewShot" alt="The Pipeline Overview in the Pixel theme, scoped to the regin project: the loop scene with feature, task and defect counts, the project panel with shipped progress and spend, the features in flight and one defect needing a decision.">
      <code>/overview</code> scoped to the <code>regin</code> project — the dashboard of the project that builds regin. 109 features, 378 tasks and 139 defects so far; the first feature in flight is the one that wrote this page.
    </BlockShot>
    <DataTable :columns="LOOP_COLUMNS" :rows="LOOP_STEPS" />

    <h2 id="example">Worked example: the Pixel theme</h2>
    <p>Feature <strong>#139</strong> asked for the Pipeline to become themeable and to ship a second, game-pixel theme next to the classic one — same routes, same data, same keys, a different look. It is a good example because it is ordinary: a UI feature specced one morning and shipped about 21 hours later, built entirely by task runs, with a dozen defects along the way.</p>
    <DataTable :columns="FACT_COLUMNS" :rows="PIXEL_FACTS" />

    <h3 id="spec">1 · Spec</h3>
    <p>The feature started as a row with a <strong>Spec step</strong>. Its spec lives on the row, not in a file: intent and non-goals in prose, the design as ten images rendered from the design file, the two theme axes (light/dark × classic/pixel) as diagrams, and 28 Gherkin scenarios — <code>switching keeps place</code>, <code>unknown stored value</code>, <code>a theme chunk that fails to load</code>, <code>colour budget</code>… Each scenario is a named test in the repo, red before the build and green at ship.</p>

    <h3 id="slices">2 · Slices become a task graph</h3>
    <p>The spec ends in a slice table. <code>regin task slice-check</code> refuses a table without falsifiable acceptance items, <code>regin task tail</code> appends the integrate → migrate → verify rows, and <code>regin task transcribe</code> turns each row into a task with dependency edges. The Pixel theme came out as fifteen slices in two waves:</p>
    <DataTable :columns="SLICE_COLUMNS" :rows="PIXEL_SLICES" />
    <BlockShot :src="graphShot" alt="The Tasks tab's graph view in the Pixel theme, scoped to feature #139: Spec and Build steps on the left, S1 Seam and S2 Pixel foundation feeding S3 Overview, S4 Tasks and S5 Features, every node marked done.">
      <code>/tasks?feature_id=139&amp;view=graph</code> — the same table as a dependency graph. S3, S4 and S5 wait only on S2, so they ran in parallel. Edges can be drawn and removed here by hand.
    </BlockShot>

    <h3 id="runs">3 · Runs</h3>
    <p>A task whose dependencies are done becomes <em>ready</em>. Arming it picks the provider (Claude through the Agent SDK, or Kimi), whether it gets its own git worktree, and the skills it should load; launching it starts a session that works on the slice’s branch and merges back into <code>f139/integration</code>. When a run reports finished, the tasks waiting on it are released — that is how S2 finishing started S3, S4 and S5 at once. You watch and steer the sessions from <RouterLink to="/runs">Runs</RouterLink>. Feature #139 took 28 such sessions and no hand-typed prompt.</p>

    <h3 id="defects">4 · Defects</h3>
    <p>Whatever the operator found wrong while looking at the result was declared as a <strong>defect</strong> against #139 — digits rendering glued together, a triage rail hiding the stats, group headers that did not follow the design. Defects can be pasted with screenshots, are triaged on the Features tab, and are fixed by a <code>fix</code> task. Closing one requires naming the check that would have caught it, so the missing gates can be ranked later.</p>
    <BlockShot :src="defectsShot" alt="The All defects list in the Pixel theme, filtered to feature #139: twelve closed defects, each with its symptom text and a reopen action.">
      <code>/defects?feature_id=139</code> — the twelve defects of the Pixel theme, all closed before it shipped.
    </BlockShot>

    <h3 id="ship">5 · Ship and count</h3>
    <p>A finished run is not a finished feature. Shipping is the operator’s act — <code>regin feature ship 139</code> — and that is the <strong>done-claim</strong>. A defect declared after it is an <em>escape</em>: it walks the feature back and adds a round. The number the pipeline is held to is <strong>rounds-to-done</strong>, computed only from declared events, never inferred from prose, branch names or timing. Cost is the sum of the sessions attributed to the feature, reported as an upper bound.</p>
    <CodeBlock :code="LEDGER" />
    <p>The same ledger is the feature’s page in the dashboard, <code>/features/139</code>: spec, slices, images, tasks, defects and sessions behind one tab strip.</p>

    <h2 id="features">The Features tab</h2>
    <p>Every feature of the project sits on a spec → build → ship track, with its task progress, rounds and open defects beside it. The rail on the right is the triage queue: defects that still need a decision.</p>
    <BlockShot :src="featuresShot" alt="The Features tab in the Pixel theme: stage filters with counts, each feature as a row with a spec-build-ship track, task progress, rounds and open defects, and the triage rail on the right.">
      <code>/features</code> for the <code>regin</code> project — stage tabs across the top, one track per feature, the triage rail on the right.
    </BlockShot>
    <Callout tone="info">
      The Pipeline ships two themes, <strong>Pixel</strong> (the default) and <strong>Classic</strong>. Switch with the picker at the right of the tab strip; the choice is per browser, like light and dark, and changes nothing but the look.
    </Callout>

    <h2 id="where">Where to find it</h2>
    <DataTable :columns="SURFACE_COLUMNS" :rows="SURFACES" />
    <p>Internals: <em>Feature pipeline</em> in <a href="https://github.com/caiuswang/regin/blob/master/ARCHITECTURE.md#feature-pipeline-features-defects-slices" class="focus-visible:ring">ARCHITECTURE.md</a>.</p>
  </DocPage>
</template>
