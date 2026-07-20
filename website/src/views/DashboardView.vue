<script setup>
import DocPage from '../components/DocPage.vue'
import Callout from '../components/Callout.vue'
import TourShot from '../components/TourShot.vue'
import sessionsShot from '../assets/shots/tour/sessions-dark.png'
import sessionsWebp from '../assets/shots/tour/sessions-dark.webp'
import liveShot from '../assets/shots/tour/live-dark.png'
import liveWebp from '../assets/shots/tour/live-dark.webp'
import inboxShot from '../assets/shots/tour/inbox-dark.png'
import inboxWebp from '../assets/shots/tour/inbox-dark.webp'
import memoryShot from '../assets/shots/tour/memory-dark.png'
import memoryWebp from '../assets/shots/tour/memory-dark.webp'
import gradesShot from '../assets/shots/tour/grades-dark.png'
import gradesWebp from '../assets/shots/tour/grades-dark.webp'
import rulesShot from '../assets/shots/tour/rules-dark.png'
import rulesWebp from '../assets/shots/tour/rules-dark.webp'
import patternsShot from '../assets/shots/tour/patterns-dark.png'
import patternsWebp from '../assets/shots/tour/patterns-dark.webp'
import topicsShot from '../assets/shots/tour/topics-dark.png'
import topicsWebp from '../assets/shots/tour/topics-dark.webp'

const TOC = [
  { id: 'traces', label: 'Session traces' },
  { id: 'live', label: 'Live steering' },
  { id: 'inbox', label: 'Inbox & messages' },
  { id: 'memory', label: 'Agent memory' },
  { id: 'grades', label: 'Grades' },
  { id: 'rules', label: 'Rules' },
  { id: 'library', label: 'Patterns & skills' },
  { id: 'topics', label: 'Topic wikis' },
]
</script>

<template>
  <DocPage
    title="The dashboard"
    lead="regin serve opens a local web dashboard on :8321. Once the hooks are wired in it fills with your real sessions. This is a tour of the surfaces you'll actually use — grouped the way the sidebar groups them: Library (what you feed the agent), Observability (what came back), and Engineering (what's enforced). Every screenshot below is the live product, not a mockup."
    :toc="TOC"
  >
    <Callout tone="info">
      Empty on first run? That's expected until you
      <RouterLink to="/getting-started#activate-hooks">install the hooks</RouterLink> —
      the dashboard only shows what the hooks capture.
    </Callout>

    <h2 id="traces">Session traces</h2>
    <p>Every Claude Code session the hooks capture lands in <strong>Trace</strong> — searchable, and filterable by repo, tag, date, and status. Each row rolls up its spans, file edits, context-window use, and elapsed-vs-active time; open one for a turn-by-turn replay with per-tool cost attribution. The sibling tabs pivot the same span stream by rule trigger, skill read, and MCP call.</p>
    <TourShot :src="sessionsShot" :webp="sessionsWebp" :width="2720" :height="1800"
      alt="The Trace session list: sub-tabs for Sessions, Rule Triggers, Skill Reads, MCP Calls; filter row; and a table of sessions with spans, edits, context %, and elapsed/active columns.">
      The session list, filtered to today. Each session carries its own span count, edit count, and context-window usage.
    </TourShot>

    <h2 id="live">Live steering</h2>
    <p>Watch a session <em>as it runs</em> — the current turn, tool calls landing in real time, a live context meter. With the <RouterLink to="/configuration#agent-bridge">agent bridge</RouterLink> enabled, the composer at the bottom sends a prompt or steering message straight into the running session, queued into the current turn.</p>
    <TourShot :src="liveShot" :webp="liveWebp" :width="960" :height="1936"
      alt="The Live session card: a running-status header with elapsed time and context meter, a real-time conversation tail with a 'running tool.Bash' now-zone, and a steering composer at the bottom.">
      The Live card — this is the tour session steering <em>itself</em>: the composer message queues into the turn that is capturing this very screenshot.
    </TourShot>

    <h2 id="inbox">Inbox &amp; messages</h2>
    <p>Everything your agents push with <code>send_to_user</code> — progress, results, warnings, blockers, and lessons — collected across every session, filterable by type, with an unread badge. It's the durable record of what each session decided and why, and it doubles as a trail you can retrace when a similar problem resurfaces.</p>
    <TourShot :src="inboxShot" :webp="inboxWebp" :width="2720" :height="1720"
      alt="The Inbox: type filters (Progress, Note, Lesson, Result, Summary, Warning, Blocker) with counts, and a grid of message cards from across sessions.">
      Messages from every session in one place — typed, searchable, and linkable back to the session that sent them.
    </TourShot>

    <h2 id="memory">Agent memory</h2>
    <p>The cross-session lesson store. Lessons captured from <code>send_to_user(type=lesson)</code> and post-session distills are ranked by usefulness, de-duplicated offline, and recalled into future prompts on demand. Browse by category, search titles and bodies, navigate the topic tree, or use the Recall tab to preview what a given task would pull.</p>
    <TourShot :src="memoryShot" :webp="memoryWebp" :width="2720" :height="1880"
      alt="The Memory page: tabs for Memories, Topics, Tree, Wikis, Recall, Doctor; category filters with counts; and a grid of lesson cards.">
      451 lessons here, browsable by category and topic — the store that survives <code>regin init</code> and feeds later sessions.
    </TourShot>

    <h2 id="grades">Grades</h2>
    <p>Post-hoc rubric grades on two axes that are <em>never</em> fused into one number: <strong>correctness</strong> (claims checked against trace evidence) and <strong>process</strong> (trajectory efficiency and cost). Paste a trace id to grade on demand — <code>screen</code> is instant, <code>deep</code> runs an LLM judge over every selected dimension, and <code>auto</code> escalates only when the screen is unsure.</p>
    <TourShot :src="gradesShot" :webp="gradesWebp" :width="2720" :height="1760"
      alt="The Grades page: stat cards for sessions graded, satisfied count, cost per correct outcome, and off-frontier; a verdict-distribution chart; and a grade-a-session form.">
      Correctness and process kept on separate axes, with a verdict distribution across graded sessions and an on-demand grader.
    </TourShot>

    <h2 id="rules">Rules</h2>
    <p>Every rule the configured engines enforce, grouped by pattern or layer, each showing its engine, trigger, severity, and deploy state. These are the gates that fire on the <code>PostToolUse</code> hook when an engine decides a changed file is applicable — the feedback half of the harness. Three engines ship built-in: GritQL, Radon (complexity), and the generic bundle engine.</p>
    <TourShot :src="rulesShot" :webp="rulesWebp" :width="2720" :height="1800"
      alt="The Rules page: engine summary chips (grit, radon, bundle packs) with counts, group-by controls, and a table of rules with engine, triggers, severity, layer, and summary.">
      31 rules across four engines, grouped by pattern. Severity and deploy state are visible at a glance.
    </TourShot>

    <h2 id="library">Patterns &amp; skills — the library</h2>
    <p>The feedforward half: procedure guides you write or import (<strong>Patterns</strong>), promoted to versioned, deployable <strong>Skill</strong> bundles surfaced to the agent only when their triggers match. Drop a skill folder to import, tag and search, then push to deploy into the active provider's skills directory. <strong>Repos</strong>, <strong>Prompts</strong>, and <strong>Skills</strong> sit alongside in the same Library group.</p>
    <TourShot :src="patternsShot" :webp="patternsWebp" :width="2720" :height="1760"
      alt="The Patterns page: an import dropzone and a table of patterns with category, tags, skill state (deployed / not deployed), and scope columns.">
      Patterns synced from your source repos, each showing whether it's been promoted and deployed as a skill, and at what scope.
    </TourShot>

    <h2 id="topics">Per-repo topic wikis</h2>
    <p>Each registered repo gets a graph of approved <strong>topics</strong>, each backed by a wiki page, plus draft proposal runs and a reference-health audit. An agent explores the repo and proposes draft topics; you approve them; drift detection flags pages whose files have moved on. At task time the relevant slices route to the agent by keyword.</p>
    <TourShot :src="topicsShot" :webp="topicsWebp" :width="2720" :height="1840"
      alt="The Topics Workspace: stat cards for approved topics, proposal runs, and broken refs; Approved / Proposals / Audit sections; and a bucketed list of wiki pages.">
      The topic workspace for one repo — 44 approved topics over its wiki graph, with proposal runs and reference health.
    </TourShot>
    <p>The design and mechanics behind topics have their own area: <RouterLink to="/topics">Topic Wikis</RouterLink>. For how it all fits together, see <RouterLink to="/architecture">Architecture</RouterLink>.</p>
  </DocPage>
</template>
