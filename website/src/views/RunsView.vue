<script setup>
import DocPage from '../components/DocPage.vue'
import Callout from '../components/Callout.vue'
import DataTable from '../components/DataTable.vue'
import ThemedShot from '../components/ThemedShot.vue'
import liveLight from '../assets/shots/live-light.png'
import liveLightWebp from '../assets/shots/live-light.webp'
import liveDark from '../assets/shots/live-dark.png'
import liveDarkWebp from '../assets/shots/live-dark.webp'

const LIVE_LIGHT = { png: liveLight, webp: liveLightWebp }
const LIVE_DARK = { png: liveDark, webp: liveDarkWebp }

const TOC = [
  { id: 'arm', label: 'Arm, then launch' },
  { id: 'providers', label: 'Providers' },
  { id: 'live', label: 'Follow and steer' },
  { id: 'inbox', label: 'Hearing back' },
  { id: 'mobile', label: 'Mobile card' },
]

const PROVIDERS = [
  { agent: 'Claude Code', modes: 'Agent SDK, or a tmux pane', notes: 'Full: questions and permission prompts are answered from the browser; queued prompts can be edited before they run.' },
  { agent: 'Kimi Code', modes: 'ACP, or a tmux pane', notes: 'Experimental. Runs are unattended (approval requests are answered cancelled) and a steer is cancel-then-re-prompt.' },
  { agent: 'Codex, generic', modes: '—', notes: 'Provider adapters exist for tracing; they cannot be armed for task runs.' },
]
const PROVIDER_COLUMNS = [
  { key: 'agent', label: 'Agent' },
  { key: 'modes', label: 'Launch modes' },
  { key: 'notes', label: 'Limits' },
]
</script>

<template>
  <DocPage
    title="Runs"
    lead="The Runs layer: a task becomes an agent run you launch from the dashboard, follow live, steer or stop — and the agent reports back to an inbox."
    :toc="TOC"
  >
    <h2 id="arm">Arm, then launch</h2>
    <p>A task is <strong>armed</strong> with a provider, a model and a launch mode, then started. The launch mode decides where the process lives: the <em>channel</em> arm runs it under <code>regin serve</code> with a typed connection (Claude through the Python Claude Agent SDK, Kimi over ACP); the <em>pane</em> arm runs it in a tmux pane on regin’s own tmux server, which survives a server restart.</p>
    <p>A run settles on the agent’s <code>regin task finish</code> report or its verify command — a turn ending is not the work being done. When it settles, the tasks that depend on it are released.</p>

    <h2 id="providers">Providers</h2>
    <DataTable :columns="PROVIDER_COLUMNS" :rows="PROVIDERS" />

    <h2 id="live">Follow and steer</h2>
    <p><code>/live</code> follows a running session: its current state, its recent steps, any question it is blocked on, with steer and Stop. Because regin owns a channel run’s process, an <code>AskUserQuestion</code> or a gated tool call is parked and answered from the browser rather than typed into a terminal. Sessions you started yourself can be steered through the <strong>agent bridge</strong> — guarded keystroke delivery into the session’s tmux pane, off by default (<RouterLink to="/configuration#agent-bridge">configuration</RouterLink>).</p>
    <ThemedShot :light="LIVE_LIGHT" :dark="LIVE_DARK" :width="1440" :height="900" alt="The regin dashboard's Live view following a running agent session: recent tool calls, a Bash permission prompt waiting for an answer, the current turn, and the steer composer.">
      <code>/live</code> following the run that captured this screenshot. Its own capture command is parked as a <strong>permission</strong> card waiting for an answer, with cancel turn, stop agent and the steer box below it.
    </ThemedShot>

    <h2 id="inbox">Hearing back</h2>
    <p>Agents report progress, results, blockers and lessons through the <code>send_to_user</code> tool. Messages land in <code>/inbox</code> with a live unread badge, and high-severity ones can fan out to a webhook, Telegram or Lark. CLI: <code>regin messages</code>, <code>regin bridge</code>, <code>regin events</code>.</p>

    <h2 id="mobile">Mobile card</h2>
    <Callout tone="warn">
      Experimental. <code>/live</code> renders a phone-sized card that can answer permission prompts while you are away from the desk. Expect rough edges and breaking changes.
    </Callout>
  </DocPage>
</template>
