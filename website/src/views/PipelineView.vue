<script setup>
import DocPage from '../components/DocPage.vue'
import CodeBlock from '../components/CodeBlock.vue'
import DataTable from '../components/DataTable.vue'

const TOC = [
  { id: 'feature', label: 'A feature is a row' },
  { id: 'slices', label: 'From spec to tasks' },
  { id: 'done-claim', label: 'Done is a claim' },
  { id: 'rounds', label: 'Rounds-to-done' },
  { id: 'where', label: 'Where to find it' },
]

const SLICE_STEPS = [
  { step: 'slice', cmd: 'regin task slice-check', rule: 'Refuses a spec with no falsifiable acceptance item, and a schema change split across two slices.' },
  { step: 'tail', cmd: 'regin task tail', rule: 'Appends the integrate → migrate → verify rows from .regin/commands.json.' },
  { step: 'transcribe', cmd: 'regin task transcribe', rule: 'Turns the slice table into tasks and their dependency graph. Never arms anything.' },
  { step: 'run', cmd: 'arm, then launch', rule: 'A human act. When a task finishes, the tasks that depend on it are re-checked and released.' },
  { step: 'settle', cmd: 'verify_cmd', rule: 'The attempt’s verify command decides; the agent’s own report counts only when there is none.' },
]
const STEP_COLUMNS = [
  { key: 'step', label: 'Step' },
  { key: 'cmd', label: 'How', code: true },
  { key: 'rule', label: 'Rule' },
]

const SURFACES = [
  { where: '/overview', what: 'The Pipeline: Overview, Features and Tasks behind one tab strip; defects are reached from the Overview and the Features triage rail.' },
  { where: '/features/:id', what: 'One feature’s ledger — stage, rounds, sessions, cost, defects.' },
  { where: 'regin feature', what: 'Where a feature stands and what it cost: new, spec, stage, ledger, ship, reopen, abandon.' },
  { where: 'regin task', what: 'Capture and inspect tasks; slice-check, tail and transcribe a spec.' },
  { where: 'regin defects', what: 'What a feature got wrong, and what escaped: open, list, rounds, close.' },
]
const SURFACE_COLUMNS = [
  { key: 'where', label: 'Where', code: true },
  { key: 'what', label: 'What it shows' },
]

const LEDGER = `$ regin feature stage 153
#153  building  rounds=1  open_defects=0  escapes=0
  being built`
</script>

<template>
  <DocPage
    title="Pipeline"
    lead="The Workbench layer: a feature carries its spec and acceptance tests, is cut into tasks that run in dependency order, and is held to a done-claim. Whatever turns out wrong afterwards is a defect declared against it."
    :toc="TOC"
  >
    <h2 id="feature">A feature is a row</h2>
    <p>Each piece of work you build with an agent starts as a <strong>feature</strong>. Its spec lives on the feature row (or on its tracker issue, when the feature is tracked remotely) — not in a <code>docs/</code> file nothing re-reads. The spec’s acceptance items are named tests in the repo: red before the build, green at ship.</p>

    <h2 id="slices">From spec to tasks</h2>
    <p>The spec ends in a slice table. Each slice becomes a <strong>task</strong> with its dependencies, and tasks run in dependency order — an independent slice runs in parallel with its siblings.</p>
    <DataTable :columns="STEP_COLUMNS" :rows="SLICE_STEPS" />

    <h2 id="done-claim">Done is a claim</h2>
    <p>A finished run is not the feature being done. Shipping is the operator’s act (<code>regin feature ship</code>), and that is the <strong>done-claim</strong>. Anything found wrong after it is a <strong>defect</strong> declared against the feature; closing one requires naming the check that would have caught it, so the missing gates can be ranked.</p>

    <h2 id="rounds">Rounds-to-done</h2>
    <p>The number the pipeline is held to is <strong>rounds-to-done</strong>: one build, plus each time a done-claim was walked back. Only declarations feed it — nothing is inferred from prose, branch names or timing. Cost is the sum of the sessions attributed to the feature, reported as an upper bound.</p>
    <CodeBlock :code="LEDGER" />

    <h2 id="where">Where to find it</h2>
    <DataTable :columns="SURFACE_COLUMNS" :rows="SURFACES" />
    <p>Internals: <em>Feature pipeline</em> in <a href="https://github.com/caiuswang/regin/blob/master/ARCHITECTURE.md#feature-pipeline-features-defects-slices" class="focus-visible:ring">ARCHITECTURE.md</a>. Runs are launched from the dashboard — see <RouterLink to="/runs">Runs</RouterLink>.</p>
  </DocPage>
</template>
