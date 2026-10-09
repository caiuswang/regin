<script setup>
import DocPage from '../components/DocPage.vue'
import Callout from '../components/Callout.vue'
import TourShot from '../components/TourShot.vue'
import ThemedShot from '../components/ThemedShot.vue'
import overviewLight from '../assets/shots/pipeline-overview-light.png'
import overviewLightWebp from '../assets/shots/pipeline-overview-light.webp'
import overviewDark from '../assets/shots/pipeline-overview-dark.png'
import overviewDarkWebp from '../assets/shots/pipeline-overview-dark.webp'
import sessionsShot from '../assets/shots/tour/sessions-dark.png'
import sessionsWebp from '../assets/shots/tour/sessions-dark.webp'
import liveLight from '../assets/shots/live-light.png'
import liveLightWebp from '../assets/shots/live-light.webp'
import liveDark from '../assets/shots/live-dark.png'
import liveDarkWebp from '../assets/shots/live-dark.webp'
import inboxShot from '../assets/shots/tour/inbox-dark.png'
import inboxWebp from '../assets/shots/tour/inbox-dark.webp'
import memoryShot from '../assets/shots/memory-dark.png'
import memoryWebp from '../assets/shots/memory-dark.webp'
import rulesShot from '../assets/shots/tour/rules-dark.png'
import rulesWebp from '../assets/shots/tour/rules-dark.webp'
import patternsShot from '../assets/shots/tour/patterns-dark.png'
import patternsWebp from '../assets/shots/tour/patterns-dark.webp'
import topicsShot from '../assets/shots/tour/topics-dark.png'
import topicsWebp from '../assets/shots/tour/topics-dark.webp'

const OVERVIEW_LIGHT = { png: overviewLight, webp: overviewLightWebp }
const OVERVIEW_DARK = { png: overviewDark, webp: overviewDarkWebp }
const LIVE_LIGHT = { png: liveLight, webp: liveLightWebp }
const LIVE_DARK = { png: liveDark, webp: liveDarkWebp }

const TOC = [
  { id: 'pipeline', label: 'Pipeline' },
  { id: 'traces', label: 'Session traces' },
  { id: 'live', label: 'Live steering' },
  { id: 'inbox', label: 'Inbox & messages' },
  { id: 'memory', label: 'Agent memory' },
  { id: 'rules', label: 'Rules' },
  { id: 'library', label: 'Patterns & skills' },
  { id: 'topics', label: 'Topic wikis' },
]
</script>

<template>
  <DocPage
    title="The dashboard"
    lead="regin serve opens a local web dashboard on :8321. Once the hooks are wired in it fills with your real sessions. This is a tour of the surfaces you'll actually use, roughly in sidebar order: Workbench (features, tasks and the Inbox), Observability (what the agents did), Library (what you feed them) and Engineering (what's enforced). Every screenshot below is the live product, not a mockup."
    :toc="TOC"
  >
    <Callout tone="info">
      Empty on first run? That's expected until you
      <RouterLink to="/getting-started#activate-hooks">install the hooks</RouterLink> —
      the dashboard only shows what the hooks capture.
    </Callout>

    <h2 id="pipeline">Pipeline</h2>
    <p>The first entry in the sidebar, and where the day starts. <strong>Pipeline</strong> puts Overview, Features and Tasks behind one tab strip, scoped to a project: features on their spec → build → ship track, tasks as a list, a board or a dependency graph, and the defects declared against them. It wears the <em>Pixel</em> theme by default; Classic is one click away in the tab strip. The <RouterLink to="/pipeline">Pipeline page</RouterLink> follows one feature through it end to end.</p>
    <ThemedShot :light="OVERVIEW_LIGHT" :dark="OVERVIEW_DARK" :width="1440" :height="900" alt="The Pipeline Overview in the Pixel theme, scoped to the regin project: loop scene with feature, task and defect counts, project panel, features in flight and a defect needing a decision.">
      The Overview scoped to the <code>regin</code> project.
    </ThemedShot>

    <h2 id="traces">Session traces</h2>
    <p>Every Claude Code session the hooks capture lands in <strong>Trace</strong> — searchable, and filterable by repo, tag, date, and status. Each row rolls up its spans, file edits, context-window use, and elapsed-vs-active time; open one for a turn-by-turn replay with per-tool cost attribution. The sibling tabs pivot the same span stream by rule trigger, skill read, and MCP call. A session can also be graded after the fact, on correctness and process kept as separate axes: <code>regin grade run</code>, with the judge and aspects set under <strong>Settings → Grading</strong>.</p>
    <TourShot :src="sessionsShot" :webp="sessionsWebp" :width="2880" :height="1800"
      alt="The Trace session list: sub-tabs for Sessions, Rule Triggers, Skill Reads, MCP Calls; filter row; and a table of sessions with spans, edits, context %, and elapsed/active columns.">
      The session list, filtered to today. Each session carries its own span count, edit count, and context-window usage.
    </TourShot>

    <h2 id="live">Live steering</h2>
    <p>Watch a session <em>as it runs</em> — the current turn, tool calls landing in real time, a live context meter. With the <RouterLink to="/configuration#agent-bridge">agent bridge</RouterLink> enabled, the composer at the bottom sends a prompt or steering message straight into the running session, queued into the current turn.</p>
    <ThemedShot :light="LIVE_LIGHT" :dark="LIVE_DARK" :width="1440" :height="900" alt="The Live view following a running session: the conversation tail with tool calls and timestamps, the operator's latest prompt, the turn in progress with cancel turn and stop agent, and the steer composer at the bottom.">
      <code>/live</code> following the session that captured this tour, mid-turn: its tail, the prompt that asked for these screenshots, and the steer box.
    </ThemedShot>

    <h2 id="inbox">Inbox &amp; messages</h2>
    <p>Everything your agents push with <code>send_to_user</code> — progress, results, warnings, blockers, and lessons — collected across every session, filterable by type, with an unread badge. It's the durable record of what each session decided and why, and it doubles as a trail you can retrace when a similar problem resurfaces.</p>
    <TourShot :src="inboxShot" :webp="inboxWebp" :width="2880" :height="1800"
      alt="The Inbox: type filters (Progress, Note, Lesson, Result, Summary, Warning, Blocker) with counts, and a grid of message cards from across sessions.">
      Messages from every session in one place — typed, searchable, and linkable back to the session that sent them.
    </TourShot>

    <h2 id="memory">Agent memory</h2>
    <p>The cross-session lesson store. Lessons captured from <code>send_to_user(type=lesson)</code> and post-session distills are ranked by usefulness, de-duplicated offline, and recalled into future prompts on demand. Browse by category, search titles and bodies, navigate the topic tree, or use the Recall tab to preview what a given task would pull.</p>
    <TourShot :src="memoryShot" :webp="memoryWebp" :width="2880" :height="1800"
      alt="The Memory page: tabs for Memories, Topics, Tree, Wikis, Recall, Doctor; category filters with counts; and a grid of lesson cards.">
      1,077 active memories here, browsable by category and topic — the store that survives <code>regin init</code> and feeds later sessions.
    </TourShot>

    <h2 id="rules">Rules</h2>
    <p>Every rule the configured engines enforce, grouped by pattern or layer, each showing its engine, trigger, severity, and deploy state. These are the gates that fire on the <code>PostToolUse</code> hook when an engine decides a changed file is applicable — the feedback half of the harness. Three engines ship built-in: GritQL, Radon (complexity), and the generic bundle engine.</p>
    <TourShot :src="rulesShot" :webp="rulesWebp" :width="2880" :height="1800"
      alt="The Rules page: engine summary chips (grit, radon, bundle packs) with counts, group-by controls, and a table of rules with engine, triggers, severity, layer, and summary.">
      43 rules across five engines — grit, radon and three bundle packs — grouped by pattern. Severity and deploy state are visible at a glance.
    </TourShot>

    <h2 id="library">Patterns &amp; skills — the library</h2>
    <p>The feedforward half: procedure guides you write or import (<strong>Patterns</strong>), promoted to versioned, deployable <strong>Skill</strong> bundles surfaced to the agent only when their triggers match. Drop a skill folder to import, tag and search, then push to deploy into the active provider's skills directory. <strong>Repos</strong>, <strong>Prompts</strong>, and <strong>Skills</strong> sit alongside in the same Library group.</p>
    <TourShot :src="patternsShot" :webp="patternsWebp" :width="2880" :height="1800"
      alt="The Patterns page: an import dropzone and a table of patterns with category, tags, skill state (deployed / not deployed), and scope columns.">
      Patterns synced from your source repos, each showing whether it's been promoted and deployed as a skill, and at what scope.
    </TourShot>

    <h2 id="topics">Per-repo topic wikis</h2>
    <p>Each registered repo gets a graph of approved <strong>topics</strong>, each backed by a wiki page, plus draft proposal runs and a reference-health audit. An agent explores the repo and proposes draft topics; you approve them; drift detection flags pages whose files have moved on. At task time the relevant slices route to the agent by keyword.</p>
    <TourShot :src="topicsShot" :webp="topicsWebp" :width="2880" :height="1800"
      alt="The Topics Workspace: stat cards for approved topics, proposal runs, and broken refs; Approved / Proposals / Audit sections; and a bucketed list of wiki pages.">
      The topic workspace for one repo — 50 approved topics over its wiki graph, with proposal runs and reference health.
    </TourShot>
    <p>The design and mechanics behind topics have their own area: <RouterLink to="/topics">Topic Wikis</RouterLink>. For how it all fits together, see <RouterLink to="/architecture">Architecture</RouterLink>.</p>
  </DocPage>
</template>
