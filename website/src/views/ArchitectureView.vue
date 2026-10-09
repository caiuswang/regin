<script setup>
import DocPage from '../components/DocPage.vue'
import CodeBlock from '../components/CodeBlock.vue'
import DataTable from '../components/DataTable.vue'
import Callout from '../components/Callout.vue'

const TOC = [
  { id: 'overview', label: 'How it works' },
  { id: 'modules', label: 'Modules by layer' },
  { id: 'tracing', label: 'One capture vocabulary' },
  { id: 'ledger', label: 'Counts come from events' },
  { id: 'guidance', label: 'The Guidance layer' },
  { id: 'databases', label: 'Databases' },
]

const DIAGRAM = `              Workbench                      Runs                    Observability
   feature row ─▶ spec ─▶ slices ─▶ tasks ─▶ armed run ─────────▶  session trace
        ▲          (features.body)   (DAG)    Claude: Agent SDK      (spans, turns,
        │                                     Kimi:   ACP / tmux      tokens, grades)
        │                                        │  ▲                      │
   defects ◀── done-claim ◀──────────────────────┘  │ steer / stop         │
   (rounds-to-done)                    /live, agent bridge, inbox ◀────────┘
                                                    │
                  Guidance (underneath every run)   ▼
   patterns & skills ─▶ provider skills dir     hook_manager ◀─ rule engines (Grit, Radon, bundles)
   agent memory, topic wikis ─▶ recalled into prompts on demand

   All four layers share one Flask API + SQLite store, rendered by the Vue 3 SPA (frontend/).`

const MODULE_COLUMNS = [
  { key: 'layer', label: 'Layer' },
  { key: 'mod', label: 'Module', code: true },
  { key: 'desc', label: 'Purpose' },
]
const MODULES = [
  { layer: 'Workbench', mod: 'lib/features/', desc: 'Feature rows, the spec on the row, the append-only event log (specced, build_started, shipped, restarted, abandoned), Spec/Build steps, the ledger.' },
  { layer: 'Workbench', mod: 'lib/defects/', desc: 'Defects against a feature, triage, escapes blamed on a ship, close-with-a-check.' },
  { layer: 'Workbench', mod: 'lib/tasks/', desc: 'Tasks, dependency edges, slice-check / tail / transcribe, arming, and releasing dependents when a run finishes.' },
  { layer: 'Workbench', mod: 'lib/pipeline/', desc: 'The Pipeline shell’s reads: tab counts under one project scope, per-project rollups.' },
  { layer: 'Runs', mod: 'lib/agent_sdk/', desc: 'Claude task runs driven through the Agent SDK: launch, turns, steer, stop.' },
  { layer: 'Runs', mod: 'lib/agent_acp/', desc: 'Kimi task runs over ACP (a tmux arm also exists).' },
  { layer: 'Runs', mod: 'lib/agent_bridge/', desc: 'Delivers a human or system message into a live session.' },
  { layer: 'Runs', mod: 'lib/agent_messages/', desc: 'send_to_user: the Inbox, lessons into memory, and the optional push channels.' },
  { layer: 'Observability', mod: 'lib/agent_events/', desc: 'Producer-neutral events: hooks and the SDK project onto one span shape.' },
  { layer: 'Observability', mod: 'lib/trace/', desc: 'Span ingest, serve-time merge, turn and token rollups, the session viewer’s queries, span gates.' },
  { layer: 'Observability', mod: 'lib/grader/', desc: 'Post-hoc session rubric grades (screen + deep tiers).' },
  { layer: 'Guidance', mod: 'lib/patterns/ + lib/skills/', desc: 'Procedure guides, promoted to skills and deployed into the provider’s skills directory.' },
  { layer: 'Guidance', mod: 'lib/rule_engines/', desc: 'GritQL, Radon and bundle engines, enforced from PostToolUse hooks.' },
  { layer: 'Guidance', mod: 'lib/memory/', desc: 'Cross-session lessons: capture, recall, reflect, supersede.' },
  { layer: 'Guidance', mod: 'lib/topics/', desc: 'Per-repo topic graph: routing, proposals, drift, wikis.' },
  { layer: 'Shared', mod: 'hook_manager/', desc: 'One entry point per agent hook event; runs every handler and merges their answers.' },
  { layer: 'Shared', mod: 'lib/providers/', desc: 'Provider adapters (Claude, Kimi, Codex, generic): paths, hooks, launch capabilities.' },
  { layer: 'Shared', mod: 'lib/orm/ + lib/settings.py', desc: 'SQLModel layer and typed settings.' },
  { layer: 'Shared', mod: 'web/ + frontend/', desc: 'Flask JSON API and the Vue 3 SPA it serves.' },
]
</script>

<template>
  <DocPage
    title="Architecture"
    lead="A map of what runs where, organised by the same four layers as the rest of this site. The full internals are in ARCHITECTURE.md in the repository."
    :toc="TOC"
  >
    <h2 id="overview">How it works</h2>
    <p>regin follows one loop — Spec → Run → Watch → Count. The <strong>Workbench</strong> holds features and their tasks, <strong>Runs</strong> launches and steers the agents that work them, <strong>Observability</strong> records what those agents did, and <strong>Guidance</strong> sits under every run to keep the agent on-spec. The defects the operator declares feed back into the Workbench, and the loop closes there.</p>
    <CodeBlock :code="DIAGRAM" />
    <Callout tone="warn">
      Observability and the Guidance layer’s rule gates and memory injection run
      <em>inside</em> <code>hook_manager</code>, so they stay dormant until the
      dispatcher is installed into the agent’s settings file —
      <RouterLink to="/getting-started#activate-hooks">Getting Started → Activate the hooks</RouterLink>.
      Task runs launched from the dashboard are captured either way: the SDK path writes the same spans itself.
    </Callout>

    <h2 id="modules">Modules by layer</h2>
    <DataTable :columns="MODULE_COLUMNS" :rows="MODULES" />

    <h2 id="tracing">One capture vocabulary</h2>
    <p>Two producers feed the trace. The <strong>hook path</strong> observes a session you drive yourself: <code>hook_manager</code> handlers turn each hook event into an agent event. The <strong>SDK path</strong> drives a session regin launched: the Agent SDK runner turns each SDK message into the same agent event. Both project through one writer into spans, so a task run and a hand-driven session render in the same session viewer, with the same rollups and grades.</p>
    <p>Spans are append-only. De-duplication, re-parenting and ordering happen when a session is read, never by rewriting rows — a late or repeated event cannot corrupt what was already stored.</p>

    <h2 id="ledger">Counts come from events</h2>
    <p>A feature’s stage and its rounds-to-done are not stored as counters. They are queries over the feature’s event log, written only by declarations: the operator’s ship, reopen and abandon, a build run starting, a defect blamed on a ship. Nothing is inferred from commit messages, branch names or timing, so the number cannot drift from what actually happened. Cost is the sum of the sessions attributed to the feature, shown as an upper bound. See <RouterLink to="/pipeline">Pipeline</RouterLink> for a feature walked through end to end.</p>

    <h2 id="guidance">The Guidance layer</h2>
    <h3 id="feedforward">Patterns &amp; skills — before the agent acts</h3>
    <p>Patterns are local procedure guides you write or import. When one earns its keep, <code>regin pattern promote</code> packages it as a versioned <strong>skill</strong>, deployed into the active provider’s skills directory and surfaced to the agent only when its triggers match. Both are advisory: they shape what the agent <em>tends</em> to do.</p>
    <h3 id="rule-engines">Rule engines — while it acts</h3>
    <p>Rules are the enforced half. They run in the <code>PostToolUse</code> hook, so a violation is reported back to the agent in the same turn as the edit that caused it. Three engines ship built in: <strong>GritQL</strong> (structural queries over Python/Java), <strong>Radon</strong> (complexity grades) and the generic <strong>bundle engine</strong> (YAML rule packs, such as the Vue/CSS conventions used on regin’s own frontend and on this website).</p>
    <h3 id="memory">Agent memory — across sessions</h3>
    <p>A session that learns something worth keeping sends it as a lesson. Lessons live in their own SQLite database, outside any prompt, and are recalled into later sessions when they match the task. The store is curated rather than append-only: lessons are ranked by use, consolidated offline and superseded when they go stale, all browsable at <code>/memory</code>. Per-repo knowledge lives beside it as <RouterLink to="/topics">topic wikis</RouterLink>.</p>

    <h2 id="databases">Databases</h2>
    <p>Three stores with different lifecycles: the local SQLite database (<code>db/regin.db</code> — features, tasks, defects, repos, patterns, trace), the auth/audit database (the same SQLite file standalone, MySQL in shared mode), and the agent-memory database (its own SQLite file, deliberately outside the init/rebuild cycle). Schema changes ship as Alembic migrations; bring an existing database to head with <code>regin migrate</code>.</p>
  </DocPage>
</template>
