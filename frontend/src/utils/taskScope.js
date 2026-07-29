// Whose tasks a task-list surface counts, and how the rest stay explicable.
//
// A subagent keeps its OWN list in the same trace, and that list is never
// retired when the subagent finishes — so a session-wide roll-up reports a plan
// the model abandoned hours ago as current work (CAI-46: the header read
// `11/12` while the model's terminal showed 5). The badge therefore counts the
// MAIN agent's list — the one the terminal shows — while the expanded surfaces
// keep every agent's list visible, sectioned per agent with its own count, so
// the badge maps onto a section the reader can see rather than disagreeing with
// the list it opens.
//
// Input is always `task_list.final` (the server's whole-session snapshot);
// `agent_id` is `''`/absent for the main agent.

const MAIN = ''

// The roster falls back to the literal string 'agent' when it can't name a
// subagent, so it identifies nothing — a heading reading "agent" twice is worse
// than one reading an id.
const UNNAMED_LABELS = new Set(['agent', 'subagent'])

function agentIdOf(task) {
  const id = task && task.agent_id
  return id === null || id === undefined ? MAIN : String(id)
}

function isLive(task) {
  return task && task.status !== 'deleted'
}

export function countTasks(tasks) {
  let done = 0
  let inProgress = 0
  let open = 0
  for (const t of tasks) {
    if (t.status === 'completed') done += 1
    else if (t.status === 'in_progress') inProgress += 1
    else open += 1
  }
  return { total: tasks.length, done, inProgress, open }
}

// The tasks a header badge / live chip counts.
export function badgeTasks(finalTasks) {
  if (!Array.isArray(finalTasks)) return []
  const live = finalTasks.filter(isLive)
  const main = live.filter(t => agentIdOf(t) === MAIN)
  // A session whose main agent never wrote a list has only its subagents' plans
  // to report — an empty badge would hide the only plan there is.
  return main.length ? main : live
}

// 'main' / 'all' — which list the badge speaks for, or '' when there is only
// one list and nothing to disambiguate.
export function badgeScopeOf(sections) {
  if (sections.length < 2) return ''
  return sections.some(s => s.isMain) ? 'main' : 'all'
}

function baseLabel(agentId, roster) {
  if (agentId === MAIN) return 'main agent'
  const entry = (Array.isArray(roster) ? roster : [])
    .find(a => a && a.agent_id === agentId)
  const named = entry && (entry.label || entry.agent_type)
  return named && !UNNAMED_LABELS.has(named) ? named : agentId.slice(0, 8)
}

// Two subagents of the same declared type share one base label, which is
// exactly the ambiguity the sections exist to remove — those get their id back.
function labelsFor(agentIds, roster) {
  const base = agentIds.map(id => baseLabel(id, roster))
  const seen = new Map()
  for (const label of base) seen.set(label, (seen.get(label) || 0) + 1)
  return base.map((label, i) => (
    seen.get(label) > 1 ? `${label} ${agentIds[i].slice(0, 8)}` : label))
}

// Every agent's live tasks, grouped: `[{ agent_id, label, isMain, tasks,
// summary }]`. The main agent leads; subagents follow in the order `final`
// lists them, so two agents' payload positions never braid into one list.
export function taskAgentSections(finalTasks, roster) {
  if (!Array.isArray(finalTasks)) return []
  const byAgent = new Map()
  for (const t of finalTasks) {
    if (!isLive(t)) continue
    const id = agentIdOf(t)
    if (!byAgent.has(id)) byAgent.set(id, [])
    byAgent.get(id).push(t)
  }
  const agentIds = [...byAgent.keys()]
    .sort((a, b) => (a === MAIN ? -1 : 0) - (b === MAIN ? -1 : 0))
  const labels = labelsFor(agentIds, roster)
  return agentIds.map((agentId, i) => ({
    agent_id: agentId,
    label: labels[i],
    isMain: agentId === MAIN,
    tasks: byAgent.get(agentId),
    summary: countTasks(byAgent.get(agentId)),
  }))
}
