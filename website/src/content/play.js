// Level 0 "Play the loop" — the prototype's constants verbatim (F154).
// Ledger line inks are Palette names rather than hex so the screen reads them as classes.

export const PT = '重新设计Pipeline操作界面-游戏像素风格'

export const P_TASKS = [
  ['#356', 'Spec · F139'], ['#357', 'Build · F139'], ['#358', 'S1 — Seam'], ['#359', 'S2 — Pixel foundation'],
  ['#360', 'S3 — Overview'], ['#361', 'S4 — Tasks'], ['#362', 'S5 — Features'],
  ['#363', 'S6 — Dialogs + secret stage'], ['#364', 'S7 — integrate'],
]

export const P_NODES = [
  ['S1', 'Seam'], ['S2', 'Pixel foundation'], ['S3', 'Overview'], ['S4', 'Tasks'],
  ['S5', 'Features'], ['S6', 'Dialogs'], ['S7–S9', 'integrate → verify'],
]

export const P_COLS = [[0], [1], [2, 3, 4], [5], [6]]

// per tick: [running], [done]
export const P_TIMELINE = [
  [[], []], [[0], []], [[1], [0]], [[2, 3, 4], [0, 1]], [[4], [0, 1, 2, 3]],
  [[5], [0, 1, 2, 3, 4]], [[6], [0, 1, 2, 3, 4, 5]], [[], [0, 1, 2, 3, 4, 5, 6]],
]

export const P_DEFECTS = [
  'Digits in the counters render glued together',
  'The triage rail hides the loop stats',
  'Group headers don’t follow the design',
]

export const P_LEDGER = [
  ['$ regin feature ship 139', 'dim'], ['#139  ' + PT, 'ink'],
  ['shipped  rounds=1  open_defects=0  escapes=0', 'green'], ['  completed and never walked back', 'soft'], ['', 'soft'],
  ['cost (upper bound)  $140.43 across 28 sessions', 'soft'], ['work time  7h 17m', 'soft'],
  ['human prompts  0 across 0 human sessions', 'soft'],
]

export const P_STAGES = [
  { label: 'NEW FEATURE', kicker: 'STAGE 1 · SPEC', title: 'A feature is a row.', screen: 'PIPELINE · NEW FEATURE', cmd: 'regin feature new  →  regin feature spec 139', body: 'Its spec lives on the row: intent, design images, Gherkin acceptance tests and a slice table. The Spec step is itself a task run, so an agent can draft it.' },
  { label: 'TASKS', kicker: 'STAGE 2 · SLICE', title: 'Slices become tasks.', screen: 'PIPELINE · TASKS · F139', cmd: 'regin task slice-check · tail · transcribe', body: 'slice-check refuses rows without falsifiable acceptance items; transcribe turns each row into a task with dependency edges. Only tasks with nothing upstream start ready.' },
  { label: 'GRAPH', kicker: 'STAGE 3 · RUN', title: 'Run in dependency order.', screen: 'TASKS · GRAPH VIEW', cmd: '/tasks?feature_id=139&view=graph', body: 'Ready tasks are armed and launched as agent runs. When one finishes, its dependents are released — S2 finishing started S3, S4 and S5 at once.' },
  { label: 'DEFECTS', kicker: 'STAGE 4 · COUNT', title: 'Declare what’s wrong.', screen: 'DEFECTS · F139', cmd: 'regin defects open  →  close --check', body: 'Anything found wrong is a defect against the feature, fixed by a fix task. Closing one names the check that would have caught it. Click a bug, or press the button.' },
  { label: 'SHIP', kicker: 'STAGE 5 · SHIP', title: 'Done is a claim.', screen: 'FEATURE #139 · LEDGER', cmd: 'regin feature ship 139', body: 'A finished run is not a shipped feature — shipping is the operator’s act. A defect declared after it is an escape and adds a round.' },
]

export const ACTION_LABELS = ['+ NEW FEATURE', 'TRANSCRIBE SLICES', '▶ RUN READY TASKS', 'FIX NEXT DEFECT', 'SHIP #139']

// Sprite rows: one char per 4 px cell; the palette maps chars to Palette variable names.
const ROBOT_TOP = ['....G....', '....A....', '..HHHHH..', '.HHHHHHH.', '.HEHHHEH.', '.HHHHHHH.', '..HMMMH..', 'BBBBBBBBB', 'B.BGBGB.B', '..BBBBB..']
export const ROBOT_PALETTE = { A: 'green', H: 'body', E: 'bg', M: 'dim', B: 'muted', L: 'dim', G: 'green' }
export const ROBOT_FRAME_1 = [...ROBOT_TOP, '..L...L..', '..L...L..']
export const ROBOT_FRAME_2 = [...ROBOT_TOP, '...L.L...', '..L...L..']
export const BUG_ROWS = ['..b...b..', '...b.b...', '.bbbbbbb.', 'bb.bbb.bb', '.bbbbbbb.', 'b.b...b.b']
export const BUG_PALETTE = { b: 'gold' }
