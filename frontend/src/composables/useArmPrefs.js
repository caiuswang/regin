import { CUSTOM_MODEL, CUSTOM_SKILL, armDraft } from './useTaskArm.js'

// Arming is a privileged write, so the remembered draft is deliberately narrow:
// only the fields that describe HOW an operator runs things, never which branch
// a particular task gets. A branch is named for one task — reusing it would put
// two tasks on the same checkout — and `LaunchWorktreeFields` already seeds its
// own base from whichever repo is picked.
const KEY = 'regin.tasks.arm.last'
const REMEMBERED = [
  'cwd', 'model', 'permission_mode', 'effort',
  'skill', 'skillArgs', 'verify_cmd', 'one_shot',
]

function read() {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null
  } catch {
    return null
  }
}

/** Persist the draft that the server just accepted.
 *
 * Only a successful arm is worth remembering: a draft the server refused would
 * come back to prefill the next task with the exact payload that was rejected.
 *
 * The RESOLVED model and skill are stored, never the `custom…` sentinel. The
 * sentinel names a slot in this form, not a model — storing it would let the
 * next sheet's `adoptUnlisted` read `__custom__` as an unlisted id, move it
 * into the custom input, and arm a task with `model: "__custom__"` (which
 * `lib/tasks/arm.py` accepts as a free string and only the launch would reject).
 */
export function rememberArm(draft) {
  const kept = {}
  for (const field of REMEMBERED) kept[field] = draft[field]
  kept.model = draft.model === CUSTOM_MODEL ? draft.modelCustom.trim() : draft.model
  kept.skill = draft.skill === CUSTOM_SKILL ? draft.skillCustom.trim() : draft.skill
  // Args with no skill are not a selection: the sheet hides the input the
  // moment the picker goes back to "none", so storing them would let an
  // invisible field be the sole reason the next sheet claims a carry-over.
  if (!kept.skill) kept.skillArgs = ''
  try {
    localStorage.setItem(KEY, JSON.stringify(kept))
  } catch {
    // Quota exceeded / private browsing — the arm itself already succeeded.
  }
}

export function forgetArm() {
  try {
    localStorage.removeItem(KEY)
  } catch {
    // Nothing was stored, so nothing is left to recall.
  }
}

/** Does this draft still hold anything a previous arm put there?
 *
 * Asked again after the options load, because `pruneUnlisted` can drop every
 * recalled value — an install that no longer offers the stored repo leaves a
 * blank form, and a banner over a blank form claims a carry-over that is not
 * there.
 */
export function hasRecalledValues(draft) {
  return REMEMBERED.some(field => !!draft[field])
}

/** The draft the sheet opens with, plus whether it came from the last arm.
 *
 * A task that already carries a pending attempt wins outright: those are the
 * parameters this task's next run will actually spend, and overwriting them
 * with another task's would silently re-aim an attempt the operator is only
 * reopening to read.
 */
export function seededArmDraft(task) {
  const draft = armDraft(task)
  if (task?.armed?.params) return { draft, recalled: false }
  const stored = read()
  if (!stored) return { draft, recalled: false }
  let recalled = false
  for (const field of REMEMBERED) {
    // A filed task's directory is its project's repo (`projectCwd`), never the
    // last task's: that is how a task filed under repo B came to run in A.
    if (field === 'cwd' && task?.project_id != null) continue
    const value = stored[field]
    // The sentinel names a slot in the form, not a model — refuse it on the way
    // in as well as on the way out, so a store written before `rememberArm`
    // resolved it cannot put the literal `__custom__` into the custom input.
    const sentinel = value === CUSTOM_MODEL || value === CUSTOM_SKILL
    // Args are meaningful only under a skill — the sheet hides the input when
    // the picker reads "none", and an invisible field must never be the sole
    // reason the banner claims a carry-over.
    const orphanArgs = field === 'skillArgs' && !stored.skill
    const usable = typeof value === 'string'
      ? !!value && !sentinel && !orphanArgs
      : value === true
    if (usable) {
      draft[field] = value
      recalled = true
    }
  }
  return { draft, recalled }
}

/** The working directory a task's project seeds, or null to leave it alone.
 *
 * `null` for a task with no project — the recalled cwd stands. A project whose
 * repo is not listed seeds `''` (the server working directory) rather than
 * the recall, which would be a guess about a different task's repo.
 */
export function projectCwd(task, projects) {
  if (task?.project_id == null) return null
  const repo = (projects || []).find(p => p.id === task.project_id)
  return repo?.path || ''
}

function trimSlash(path) {
  return String(path || '').replace(/\/+$/, '')
}

/** Is `cwd` the repo at `root`, or somewhere inside it? */
export function cwdWithin(cwd, root) {
  const dir = trimSlash(cwd)
  const base = trimSlash(root)
  return !!dir && !!base && (dir === base || dir.startsWith(`${base}/`))
}

/** Reset a recalled draft back to what a first-ever arm would have shown. */
export function blankArmDraft(task) {
  return armDraft({ ...task, armed: null })
}

export { KEY as ARM_PREFS_KEY, REMEMBERED as ARM_REMEMBERED_FIELDS }
