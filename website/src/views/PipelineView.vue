<script setup>
import BlockShot from '../components/blocks/BlockShot.vue'
import BlockNote from '../components/blocks/BlockNote.vue'
import PipelineTables from '../components/pipeline/PipelineTables.vue'
import FactTiles from '../components/pipeline/FactTiles.vue'
import LedgerPre from '../components/pipeline/LedgerPre.vue'
import StageHeading from '../components/pipeline/StageHeading.vue'
import PixelQuest from '../components/quest/PixelQuest.vue'
import { useDocSections } from '../components/docs/sections.js'
import { PIPELINE_SECTIONS } from '../content/pipeline.js'
import overviewShot from '../assets/shots/pixel/overview.webp'
import graphShot from '../assets/shots/pixel/taskgraph.webp'
import defectsShot from '../assets/shots/pixel/defects.webp'
import featuresShot from '../assets/shots/pixel/features.webp'

useDocSections(() => PIPELINE_SECTIONS)
</script>

<template>
  <div class="pl-page">
    <section id="s-loop" class="pl-section is-wide">
      <h2 class="pl-h2">The loop</h2>
      <PixelQuest />
      <p class="pl-p">The dashboard's <b>Pipeline</b> has three tabs — Overview, Features, Tasks — and a project picker that scopes all of them. The Overview draws the whole loop for one project: features in the workshop, tasks on the board, defects in the bug den, and what is waiting on you.</p>
      <BlockShot :src="overviewShot" alt="The Pipeline Overview in the Pixel theme, scoped to the regin project.">
        <code>/overview</code> scoped to <code>regin</code> — 109 features, 378 tasks and 139 defects so far.
      </BlockShot>
      <PipelineTables kind="loop" />
    </section>

    <section id="s-example" class="pl-section is-wide">
      <h2 class="pl-h2">Worked example: the Pixel theme</h2>
      <p class="pl-p">Feature <b>#139</b> asked for the Pipeline to become themeable and to ship a second, game-pixel theme next to the classic one — same routes, same data, same keys, a different look. It's a good example because it is ordinary: a UI feature specced one morning and shipped about 21 hours later, built entirely by task runs, with a dozen defects along the way.</p>
      <FactTiles />
    </section>

    <section id="s-spec" class="pl-section">
      <StageHeading :stage="1" title="Spec" />
      <p class="pl-p">The feature started as a row with a <b>Spec step</b>. Its spec lives on the row, not in a file: intent and non-goals in prose, the design as ten images, the two theme axes (light/dark × classic/pixel) as diagrams, and 28 Gherkin scenarios — <code>switching keeps place</code>, <code>colour budget</code>… Each scenario is a named test in the repo, red before the build and green at ship.</p>
    </section>

    <section id="s-slices" class="pl-section">
      <StageHeading :stage="2" title="Slices become a task graph" />
      <p class="pl-p"><code>regin task slice-check</code> refuses a table without falsifiable acceptance items, <code>tail</code> appends integrate → migrate → verify, and <code>transcribe</code> turns each row into a task with dependency edges. Fifteen slices, two waves:</p>
      <PipelineTables kind="slices" />
      <BlockShot :src="graphShot" alt="The Tasks graph view scoped to feature #139, every node done." cap="S3, S4 and S5 wait only on S2, so they ran in parallel. Edges can be drawn and removed here by hand." />
    </section>

    <section id="s-runs" class="pl-section">
      <StageHeading :stage="3" title="Runs" />
      <p class="pl-p">A task whose dependencies are done becomes <em>ready</em>. Arming it picks the provider, whether it gets its own git worktree, and the skills it loads; launching it starts a session on the slice's branch that merges back into <code>f139/integration</code>. When a run finishes, the tasks waiting on it are released — S2 finishing started S3, S4 and S5 at once. Feature #139 took 28 sessions and no hand-typed prompt. <RouterLink to="/runs">Runs →</RouterLink></p>
    </section>

    <section id="s-defects" class="pl-section">
      <StageHeading :stage="4" title="Defects" />
      <p class="pl-p">Whatever the operator found wrong was declared as a <b>defect</b> against #139 — digits glued together, a triage rail hiding the stats, group headers off-design. Defects are triaged on the Features tab and fixed by a <code>fix</code> task. Closing one requires naming the check that would have caught it.</p>
      <BlockShot :src="defectsShot" alt="All defects filtered to feature #139: twelve closed defects." cap="The twelve defects of the Pixel theme, all closed before it shipped." />
    </section>

    <section id="s-ship" class="pl-section">
      <StageHeading :stage="5" title="Ship and count" />
      <p class="pl-p">A finished run is not a finished feature. Shipping — <code>regin feature ship 139</code> — is the <b>done-claim</b>. A defect declared after it is an <em>escape</em>: it walks the feature back a round. The score is <b>rounds-to-done</b>, computed only from declared events.</p>
      <LedgerPre />
    </section>

    <section id="s-features" class="pl-section">
      <h2 class="pl-h2">The Features tab</h2>
      <p class="pl-p">Every feature sits on a spec → build → ship track, with task progress, rounds and open defects beside it. The rail on the right is the triage queue.</p>
      <BlockShot :src="featuresShot" alt="The Features tab in the Pixel theme with stage filters, feature tracks and the triage rail." />
      <BlockNote tone="TIP" class="pl-tip" t="Two themes ship: **Pixel** (default) and **Classic**. Switch from the picker at the right of the tab strip — per browser, look only." />
    </section>

    <section id="s-where" class="pl-section">
      <h2 class="pl-h2">Where to find it</h2>
      <PipelineTables kind="surfaces" />
    </section>
  </div>
</template>
