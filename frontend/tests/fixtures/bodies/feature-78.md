Now when arm the task to run, it's hard to operate it quickly, let the client
remember the last selection or give me good choice. After decide, you must fill
the detail to the feature doc.

## Decision (shipped @ 0bd5dcf6)

Remember the last **accepted** arm and offer it to the next never-armed task.
Client-side only — no API, no schema, no settings field.

### Root cause

`frontend/src/composables/useTaskArm.js::armDraft()` seeded a draft only from
`task.armed.params` — the task's own pending attempt. A never-armed task
therefore opened an all-blank eight-field form, so arming a batch of tasks the
same way meant re-picking cwd, worktree, permission mode, model, effort, skill,
skill args and verify command once per task.

### Types

```
ArmPreset                      # localStorage['regin.tasks.arm.last'], JSON
  cwd              : string    # '' = the server's own working directory
  model            : string    # RESOLVED id, never the `custom…` sentinel
  permission_mode  : string
  effort           : string
  skill            : string    # RESOLVED name, never the sentinel
  skillArgs        : string    # '' whenever `skill` is ''
  verify_cmd       : string
  one_shot         : boolean
```

Nothing else is stored. `autorun` and every worktree field are deliberately
absent.

### Seeding rules

| task state | preset present | draft seed | banner |
|---|---|---|---|
| has a pending armed attempt | any | that attempt | hidden |
| never armed | no | blank (pre-change behaviour) | hidden |
| never armed | yes | the preset | shown |
| never armed | yes, but every value pruned as stale | blank | hidden |

### Invariants

- **No worktree state is ever carried.** A branch is named for one task —
  reusing it would aim two tasks at one checkout — and `LaunchWorktreeFields`
  already self-seeds its base from whichever repo is picked.
- **The sentinel never round-trips.** `custom…` (`__custom__`) names a slot in
  the form, not a model. `rememberArm` stores the resolved id, and the seed
  loop refuses the sentinel on the way in as well. Storing it would arm the
  next task with the literal string `__custom__`, which `lib/tasks/arm.py`
  accepts (`_string`, not `_choice`) and only the launch would reject.
- **A preset is written only after the server accepts the arm** — never on a
  refusal, which would re-offer the exact payload that was rejected.
- **A stale value is dropped, not sent.** `pruneUnlisted` clears a stored
  `cwd` / `permission_mode` / `effort` the install no longer offers. It runs
  ONLY on a recalled draft: a value the task itself carries is a deliberate
  pick and keeps showing, stale or not.
- **The banner never lies.** It is re-derived after pruning, and an invisible
  field (orphan `skillArgs` under no skill) can never be the sole reason it
  shows. It is a safety affordance, not decoration: arming is the only browser
  write that sets execution parameters, so a carried-over
  `permission_mode` must be visible before the operator presses the button they
  always press.
- **localStorage is best-effort.** Every read/write is guarded; private mode or
  quota degrades to the pre-change behaviour and never breaks an arm.

### Acceptance (all green, `frontend/tests/tasks-and-keys.spec.js`)

- `:430` a never-armed task is filled from the last arm, visibly and undoably —
  carries the eight fields, shows `task-arm-recalled`, pins the exact stored key
  set (which is what proves no worktree carry), and Reset blanks the form AND
  removes the stored preset.
- `:496` a recalled custom model and skill carry the real ids, not the sentinel.
- `:539` an invisible field never carries, and a stored sentinel is refused.
- `:574` a preset whose every value went stale drops the banner too.

### Files

- `frontend/src/composables/useArmPrefs.js` (new) — read / write / seed / reset.
- `frontend/src/composables/useTaskArm.js` — `pruneUnlisted(draft)`.
- `frontend/src/components/tasks/TaskArmSheet.vue` — seed, banner, Reset,
  remember-on-accept.

### Rejected

- **A server-side per-user default.** The ask is "remember what I just picked",
  which is a browser affordance; a stored default is a settings feature with a
  Settings UI, a schema change and a migration behind it.
- **Auto-creating a child task per arm** — see memory `31f48486`: minting a row
  per click broke 47 tests and the deps/finish flow.

### Known, not fixed (follow-ups, not defects of this change)

- The preset is per browser **origin**, so operators sharing one browser profile
  share it. Only how-to-run fields are stored (no secrets), the server
  revalidates every arm, and the banner discloses the carry-over.
- Reset also clears worktree fields typed in the current sheet. "Reset" blanks
  the draft; the worktree fields were never recalled, so this is a scope
  mismatch rather than a bug.
