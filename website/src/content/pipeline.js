// The worked example on /pipeline: feature #139, the Pixel theme, as regin
// recorded it. Every number here is read off `regin feature ledger 139` and
// the feature's spec; refresh them together or not at all.

export const LOOP_STEPS = [
  { step: 'Spec', where: 'Features tab · Spec step', what: 'A feature row holds the spec: intent, non-goals, design images, Gherkin acceptance and a slice table. A Spec step is itself a task run, so an agent can draft it.' },
  { step: 'Slice', where: 'regin task slice-check / tail / transcribe', what: 'The slice table becomes tasks plus dependency edges. Integrate → migrate → verify rows are appended from the project’s command table.' },
  { step: 'Run', where: 'Tasks tab · arm, launch', what: 'A ready task is armed (provider, worktree, skills) and launched. When it finishes, the tasks that depend on it are re-checked and released.' },
  { step: 'Watch', where: '/live, Inbox, Trace', what: 'Each run is a live session you can steer or stop; it reports progress to the Inbox and leaves a full trace.' },
  { step: 'Ship', where: 'regin feature ship', what: 'The operator’s done-claim. A finished run is not a shipped feature.' },
  { step: 'Count', where: 'Defects, ledger', what: 'Anything found wrong is a defect against the feature. One found after the ship is an escape and walks the feature back a round.' },
]
export const LOOP_COLUMNS = [
  { key: 'step', label: 'Step' },
  { key: 'where', label: 'Where', code: true },
  { key: 'what', label: 'What happens' },
]

export const PIXEL_SLICES = [
  { slice: 'S1', name: 'Seam', deps: '—', delivers: 'Theme registry, html[data-pl-theme], slot components; classic = today’s look' },
  { slice: 'S2', name: 'Pixel foundation', deps: 'S1', delivers: 'Tokens (light + dark), Silkscreen font, sprite sheet, pixel chrome' },
  { slice: 'S3–S5', name: 'Overview · Tasks · Features', deps: 'S2', delivers: 'One tab each, built in parallel' },
  { slice: 'S6', name: 'Dialogs + secret stage', deps: 'S5', delivers: 'Create dialogs, arm sheet, picker' },
  { slice: 'S7–S9', name: 'integrate → migrate → verify', deps: 'S3, S4, S6', delivers: 'Chain tail: merge back, migrate, full test suites' },
  { slice: 'S10, S11', name: 'Scoped overview · Menus + task filter', deps: 'S9', delivers: 'Second wave, cut after the first verify' },
  { slice: 'S12 → S15', name: 'All defects, then Dark palette', deps: 'S11', delivers: 'S12 waits on S11’s menus; S15 waits on S12' },
  { slice: 'S13–S14', name: 'integrate → verify', deps: 'S10, S15', delivers: 'Second chain tail' },
]
export const SLICE_COLUMNS = [
  { key: 'slice', label: 'Slice', code: true },
  { key: 'name', label: 'Name' },
  { key: 'deps', label: 'Depends on', code: true },
  { key: 'delivers', label: 'Delivers' },
]

export const PIXEL_FACTS = [
  { fact: 'Spec', value: '10 design images, 28 Gherkin scenarios, 15 slices' },
  { fact: 'Tasks', value: '25, all done: 1 Spec step, 16 build (the Build step + 15 slices), 3 defect fixes, 5 follow-ups' },
  { fact: 'Sessions', value: '28 task runs, 0 hand-typed prompts' },
  { fact: 'Time', value: 'specced 2026-10-06 06:56 → shipped 2026-10-07 03:42; 7h 17m of active run time' },
  { fact: 'Cost', value: '≤ $140.43 (upper bound, sum of attributed sessions)' },
  { fact: 'Defects', value: '12 declared during the build, all closed before the ship' },
  { fact: 'Rounds-to-done', value: '1 — 0 escapes, never walked back' },
]
export const FACT_COLUMNS = [
  { key: 'fact', label: 'Measure' },
  { key: 'value', label: 'Feature #139' },
]

export const LEDGER = `$ regin feature ledger 139
#139  重新设计Pipeline操作界面-游戏像素风格
shipped  rounds=1  open_defects=0  escapes=0
  completed and never walked back

cost (upper bound)  $140.43 across 28 sessions
work time  7h 17m
human prompts  0 across 0 human sessions
    28 sessions excluded, produced by a task run

sessions  (4 of 28, picked: the first four slices)
  2026-10-06  85fcf73e  S1 — Seam                          $6.20  run   round —
  2026-10-06  cc2e7fcb  S2 — Pixel foundation              $4.38  run   round —
  2026-10-06  fad49391  S4 — Tasks                        $11.44  run   round —
  2026-10-06  ff040c3d  S3 — Overview                      $8.46  run   round —
  …`

export const SURFACES = [
  { where: '/overview', what: 'The loop for one project or all of them: feature, task and defect counts, the project panel, Run next, Needs a decision.' },
  { where: '/features', what: 'Every feature on its spec → build → ship track, with the triage rail for defects waiting on a decision.' },
  { where: '/tasks', what: 'Tasks as a list, a board, or a dependency graph you can edit by dragging edges.' },
  { where: '/defects', what: 'All defects, filterable by feature, status and triage.' },
  { where: '/features/:id', what: 'One feature’s ledger: spec, slices, images, tasks, defects, sessions and what they cost.' },
  { where: 'regin feature', what: 'new, spec, stage, ledger, ship, reopen, abandon.' },
  { where: 'regin task', what: 'add, list, show; slice-check, tail and transcribe a spec; finish (run by the task’s own agent).' },
  { where: 'regin defects', what: 'open, list, rounds, attribute, fix-item, close, reopen — close names the check that would have caught it.' },
]
export const SURFACE_COLUMNS = [
  { key: 'where', label: 'Where', code: true },
  { key: 'what', label: 'What it shows' },
]
