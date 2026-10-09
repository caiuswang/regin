// The Pipeline page's tables, verbatim from the prototype (Regin Docs.dc.html:
// LOOP, FACTS, SLICES, SURFACES and the ledger `pre`). Dated snapshot of F139.

export const LOOP = [
  ['Spec', 'Features tab · Spec step', 'A feature row holds the spec: intent, non-goals, design images, Gherkin acceptance and a slice table. A Spec step is itself a task run, so an agent can draft it.'],
  ['Slice', 'regin task slice-check / tail / transcribe', 'The slice table becomes tasks plus dependency edges. Integrate → migrate → verify rows are appended from the project’s command table.'],
  ['Run', 'Tasks tab · arm, launch', 'A ready task is armed (provider, worktree, skills) and launched. When it finishes, dependent tasks are re-checked and released.'],
  ['Watch', '/live, Inbox, Trace', 'Each run is a live session you can steer or stop; it reports progress to the Inbox and leaves a full trace.'],
  ['Ship', 'regin feature ship', 'The operator’s done-claim. A finished run is not a shipped feature.'],
  ['Count', 'Defects, ledger', 'Anything found wrong is a defect against the feature. One found after the ship is an escape and walks the feature back a round.'],
]

export const FACTS = [
  ['SPEC', '10 design images, 28 Gherkin scenarios, 15 slices'],
  ['TASKS', '25, all done: 1 Spec step, 16 build, 3 defect fixes, 5 follow-ups'],
  ['SESSIONS', '28 task runs, 0 hand-typed prompts'],
  ['TIME', 'Specced 2026-10-06 06:56 → shipped 2026-10-07 03:42; 7h 17m active'],
  ['COST', '≤ $140.43 (upper bound)'],
  ['DEFECTS', '12 declared during the build, all closed before ship'],
  ['ROUNDS-TO-DONE', '1 — 0 escapes, never walked back'],
]

export const SLICES = [
  ['S1', 'Seam', '—', 'Theme registry, html[data-pl-theme], slot components; classic = today’s look'],
  ['S2', 'Pixel foundation', 'S1', 'Tokens (light + dark), Silkscreen font, sprite sheet, pixel chrome'],
  ['S3–S5', 'Overview · Tasks · Features', 'S2', 'One tab each, built in parallel'],
  ['S6', 'Dialogs + secret stage', 'S5', 'Create dialogs, arm sheet, picker'],
  ['S7–S9', 'integrate → migrate → verify', 'S3, S4, S6', 'Chain tail: merge back, migrate, full test suites'],
  ['S10, S11', 'Scoped overview · Menus + filter', 'S9', 'Second wave, cut after the first verify'],
  ['S12 → S15', 'All defects, then Dark palette', 'S11', 'S12 waits on S11’s menus; S15 waits on S12'],
  ['S13–S14', 'integrate → verify', 'S10, S15', 'Second chain tail'],
]

export const SURFACES = [
  ['/overview', 'The loop for one project or all of them: counts, the project panel, Run next, Needs a decision.'],
  ['/features', 'Every feature on its spec → build → ship track, with the triage rail.'],
  ['/tasks', 'Tasks as a list, a board, or a dependency graph you edit by dragging edges.'],
  ['/defects', 'All defects, filterable by feature, status and triage.'],
  ['/features/:id', 'One feature’s ledger: spec, slices, images, tasks, defects, sessions, cost.'],
  ['regin feature', 'new, spec, stage, ledger, ship, reopen, abandon.'],
  ['regin task', 'add, list, show; slice-check, tail, transcribe; finish.'],
  ['regin defects', 'open, list, rounds, attribute, fix-item, close, reopen.'],
]

// `prompt` lines get the dim "$ ", `ok` lines are green.
export const LEDGER = [
  { t: 'regin feature ledger 139', prompt: true },
  { t: '#139  重新设计Pipeline操作界面-游戏像素风格' },
  { t: 'shipped  rounds=1  open_defects=0  escapes=0', ok: true },
  { t: '  completed and never walked back' },
  { t: '' },
  { t: 'cost (upper bound)  $140.43 across 28 sessions' },
  { t: 'work time  7h 17m' },
  { t: 'human prompts  0 across 0 human sessions' },
  { t: '' },
  { t: 'sessions  (4 of 28)' },
  { t: '  2026-10-06  85fcf73e  S1 — Seam               $6.20' },
  { t: '  2026-10-06  cc2e7fcb  S2 — Pixel foundation   $4.38' },
  { t: '  2026-10-06  fad49391  S4 — Tasks             $11.44' },
  { t: '  2026-10-06  ff040c3d  S3 — Overview           $8.46' },
  { t: '  …' },
]

export const PIPELINE_SECTIONS = [
  { id: 's-loop', label: 'The loop' },
  { id: 's-example', label: 'Worked example' },
  { id: 's-spec', label: 'Spec' },
  { id: 's-slices', label: 'Slices → task graph' },
  { id: 's-runs', label: 'Runs' },
  { id: 's-defects', label: 'Defects' },
  { id: 's-ship', label: 'Ship and count' },
  { id: 's-features', label: 'Features tab' },
  { id: 's-where', label: 'Where to find it' },
]
