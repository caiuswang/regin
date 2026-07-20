<script setup>
import DocPage from '../components/DocPage.vue'
import CodeBlock from '../components/CodeBlock.vue'
import DataTable from '../components/DataTable.vue'
import Callout from '../components/Callout.vue'

const TOC = [
  { id: 'overview', label: 'How it works' },
  { id: 'feedforward', label: 'Patterns & skills' },
  { id: 'rule-engines', label: 'Rule engines' },
  { id: 'tracing', label: 'Session tracing' },
  { id: 'memory', label: 'Agent memory' },
  { id: 'topics', label: 'Topic wikis' },
  { id: 'databases', label: 'Databases' },
]

const MODULE_COLUMNS = [
  { key: 'mod', label: 'Module', code: true },
  { key: 'desc', label: 'Purpose' },
]
const MODULES = [
  { mod: 'lib/settings.py', desc: 'Typed settings (pydantic-settings): env > settings.local.json > settings.json > defaults.' },
  { mod: 'hook_manager/', desc: 'Hook dispatcher: one entry point per Claude hook event, running every registered handler and merging their responses.' },
  { mod: 'lib/rule_engines/', desc: 'RuleEngine protocol + built-in adapters: GritQL, Radon (complexity), and the generic bundle engine.' },
  { mod: 'lib/trace/', desc: 'Span ingest, aggregates, and the queries behind the session viewer.' },
  { mod: 'lib/memory/', desc: 'Cross-session agent memory: capture, distill, recall, reflect, feedback.' },
  { mod: 'lib/topics/', desc: 'Topic graph: routing, proposals, drift detection, wikis.' },
  { mod: 'lib/grader/', desc: 'Post-hoc session rubric grader (screen + deep tiers).' },
  { mod: 'lib/providers/', desc: 'Provider adapters (claude, codex, generic, kimi) for skills/hooks/session path conventions.' },
  { mod: 'web/ + frontend/', desc: 'Flask JSON API + the Vue 3 SPA dashboard it serves.' },
]
</script>

<template>
  <DocPage
    title="Architecture"
    lead="A map of the system for users who want to know what runs where. For full internals see ARCHITECTURE.md in the repository; the deepest subsystems each carry their own topic wiki."
    :toc="TOC"
  >
    <h2 id="overview">How it works</h2>
    <p>regin manages three asset classes — patterns (markdown procedure guides), rule engines, and trace (session + span data) — and deploys them into the active provider's skills directory so the agent reads them at the right moment.</p>
    <CodeBlock :code="'Pattern guides (SKILL.md)  +  Rule engines (.grit, …)\n            |                          |\n            v                          v\n   SQLite tracking  +  Flask API  +  hook_manager\n                        |\n                        v\n               Vue 3 SPA (frontend/)\n                        |\n                        v\n   Active provider skills dir (~/.claude/skills/)'" />
    <DataTable :columns="MODULE_COLUMNS" :rows="MODULES" />
    <Callout tone="warn">
      The feedback and observability layers below (rule gates, session tracing,
      memory injection) run <em>inside</em> <code>hook_manager</code>, so they stay
      dormant until you install that dispatcher into the agent's settings file. New
      instances do this on
      <RouterLink to="/getting-started#activate-hooks">Getting Started → Activate the hooks</RouterLink>.
    </Callout>

    <h2 id="feedforward">Patterns &amp; skills — the feedforward layer</h2>
    <p>Raw → refined, in two stages. <strong>Patterns</strong> are local procedure guides you write or import — opinionated, local to your machine, edited as your codebase evolves. When one earns its keep, <code>pattern promote</code> packages it as a <strong>skill</strong>: versioned, publishable, deployed across machines, and surfaced to the agent only when its trigger conditions match. Both are advisory: they shape what the agent <em>tends</em> to do.</p>

    <h2 id="rule-engines">Rule engines — the feedback layer</h2>
    <p>Rules are the enforced half. They live in real hooks that intercept tool calls, edits, and prompts in flight and either push the agent back on-spec or block the action outright — the agent can't drift past them because the harness, not the agent, makes the call. Three engines ship built-in: <strong>GritQL</strong> (structural queries over Python/Java), <strong>Radon</strong> (cyclomatic-complexity gates), and the generic <strong>bundle engine</strong> (YAML rule packs, like the Vue/CSS style conventions used on regin's own frontend — and on this website).</p>

    <h2 id="tracing">Session tracing — the observability layer</h2>
    <p>Hooks and transcripts produce a stream of events: tool calls, model thoughts, file edits, hook decisions, token usage. regin ingests that stream into OpenTelemetry-style spans and renders a real session viewer — filterable by tool, agent, and phase; replayable; with rollups (tokens by tool, skill reads, rule triggers) so you can diagnose <em>why</em> a session went sideways.</p>

    <h2 id="memory">Agent memory — cross-session learning</h2>
    <p>When a session discovers something worth keeping, it's distilled into a lesson — explicitly when the agent flags one, or automatically by a post-session distiller — and written to a store that lives outside any single prompt (its own SQLite database, so it survives <code>regin init</code>). On later tasks the relevant lessons are recalled and injected on demand. The store is curated, not append-only: lessons are ranked by usefulness, reinforced when recalled, de-duplicated offline, and superseded as they go stale — all browsable from the <code>/memory</code> page.</p>

    <h2 id="topics">Topic wikis — per-repo knowledge</h2>
    <p>Each registered repo gets a graph of topics, each carrying a wiki page. A tool-using agent explores the repo and <em>proposes</em> draft topics; you review and approve them; content-drift detection flags pages whose underlying files have moved on. At task time topics are routed to the agent by keyword match — only the slices touching what it's doing right now. The full design has its own section: <RouterLink to="/topics">Topic Wikis</RouterLink>.</p>

    <h2 id="databases">Databases</h2>
    <p>Three stores with different lifecycles: the local SQLite cache (<code>db/regin.db</code> — pattern index, repos, trace; rebuildable from disk with <code>regin rebuild</code>), the auth/audit database (same SQLite file in standalone mode, MySQL in shared mode), and the agent-memory database (its own SQLite file, deliberately outside the init/rebuild cycle). Schema changes ship as Alembic migrations; bring an existing DB to head with <code>regin migrate</code>.</p>
  </DocPage>
</template>
