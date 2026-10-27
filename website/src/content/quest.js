// The Pipeline quest map: levels on a 30 × 18 tile grid, the roads between
// them, and the script the robot plays. Copy follows the Pipeline page.

export const MAP = { cols: 30, rows: 18, tile: 16 }

export const LEVELS = [
  {
    id: 'spec', code: '1-1', name: 'Spec', kind: 'house', roof: 'green', x: 4, y: 4,
    detail: 'A feature is a row. Its spec lives on the row: intent, design images, Gherkin acceptance tests and a slice table. Each scenario is a named test, red before the build.',
  },
  {
    id: 'slice', code: '1-2', name: 'Slice', kind: 'house', roof: 'deep', x: 11, y: 4,
    detail: 'regin task slice-check refuses a table without falsifiable acceptance items; tail appends integrate → migrate → verify.',
  },
  {
    id: 'transcribe', code: '1-3', name: 'Transcribe', kind: 'library', roof: 'soft', x: 18, y: 4,
    detail: 'transcribe turns each slice row into a task with dependency edges. Only tasks with nothing upstream start ready.',
  },
  {
    id: 'arm', code: '1-4', name: 'Arm', kind: 'tower', x: 25, y: 4,
    detail: 'Arming is a human act: it picks the provider — Claude Code through the Agent SDK, Kimi or CodeBuddy over ACP, any of them in a tmux pane — the worktree, and the skills the run loads.',
  },
  {
    id: 'run', code: '1-5', name: 'Run', kind: 'forge', roof: 'deep', x: 25, y: 10,
    detail: 'The run is a live session you can steer or stop. It reports to the Inbox and leaves a full trace. When it finishes, the tasks waiting on it are released.',
  },
  {
    id: 'finish', code: '1-6', name: 'Finish', kind: 'chest', x: 17, y: 10,
    detail: 'The agent reports task finish itself; a session that ends without it settles failed. A finished run is not a finished feature.',
  },
  {
    id: 'ship', code: '1-7', name: 'Ship', kind: 'castle', x: 8, y: 10,
    detail: 'regin feature ship is the operator’s done-claim. The score is rounds-to-done, computed only from declared events.',
  },
  {
    id: 'defect', code: 'BOSS', name: 'Defect', kind: 'cave', x: 8, y: 15,
    detail: 'A defect declared after the ship is an escape: it is blamed on that ship and walks the feature back a round.',
  },
  {
    id: 'fix', code: '1-8', name: 'Fix', kind: 'smithy', roof: 'gold', x: 17, y: 15,
    detail: 'A fix task repairs it. Closing a defect requires naming the check that would have caught it.',
  },
]

export const ROADS = [
  ['spec', 'slice', 'main'],
  ['slice', 'transcribe', 'main'],
  ['transcribe', 'arm', 'main'],
  ['arm', 'run', 'main'],
  ['run', 'finish', 'main'],
  ['finish', 'ship', 'main'],
  ['ship', 'defect', 'rework'],
  ['defect', 'fix', 'rework'],
  ['fix', 'run', 'rework'],
]

export const QUEST = [
  { log: 'specced', say: ['spec', 'A new feature row. The spec is written; the quest begins.'] },
  { walk: 'slice', say: ['slice', 'slice-check: no problems. Every acceptance item has one owner.'] },
  { walk: 'transcribe', say: ['transcribe', 'Slices become tasks with dependency edges.'] },
  { walk: 'arm', say: ['arm', 'Armed: provider, worktree and skills picked by a human.'] },
  { walk: 'run', log: 'build_started', say: ['run', 'The run starts. Every tool call lands in the trace.'] },
  { walk: 'finish', chest: true, say: ['finish', 'task finish — the agent reports its own result.'] },
  { walk: 'ship', log: 'shipped', say: ['ship', 'feature ship: the done-claim is on the record.'] },
  { spawn: true, log: 'defect → blamed on ship', say: ['defect', 'A defect was declared after the ship. That is an escape.'] },
  { walk: 'defect', fight: true, say: ['defect', 'Cornered. The missing check is named.'] },
  { walk: 'fix', say: ['fix', 'A fix task, test first.'] },
  { walk: 'run', log: 'restarted', round: true, say: ['run', 'Running again costs a round: rounds-to-done = 2.'] },
  { walk: 'finish', chest: true },
  { walk: 'ship', log: 'shipped', victory: true, say: ['ship', 'Shipped clean. Done in 2 rounds.'] },
  { rest: 5 },
]
