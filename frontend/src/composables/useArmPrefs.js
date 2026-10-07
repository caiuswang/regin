import { CUSTOM_MODEL, DEFAULT_PROVIDER, armDraft } from './useTaskArm.js'

// Arming is a privileged write, so the remembered draft is deliberately narrow:
// only the fields that describe HOW an operator runs things, never which branch
// a particular task gets. A branch is named for one task — reusing it would put
// two tasks on the same checkout — and `LaunchWorktreeFields` already seeds its
// own base from whichever repo is picked. A verify command is one task's too:
// it is what proves THAT task done, and carried into the next sheet it is
// armed unseen and settles an unrelated run.
const KEY = 'regin.tasks.arm.last'
// `provider` leads: a remembered model is only recalled under the provider it
// was picked for.
const REMEMBERED = [
  'provider', 'cwd', 'model', 'permission_mode', 'effort',
  'skills', 'skillArgs', 'one_shot',
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
 * The RESOLVED model is stored, never the `custom…` sentinel. The sentinel
 * names a slot in this form, not a model — storing it would let the next
 * sheet's `adoptUnlisted` read `__custom__` as an unlisted id, move it into
 * the custom input, and arm a task with `model: "__custom__"` (which
 * `lib/tasks/arm.py` accepts as a free string and only the launch would reject).
 */
export function rememberArm(draft) {
  const kept = {}
  for (const field of REMEMBERED) kept[field] = draft[field]
  kept.model = draft.model === CUSTOM_MODEL ? draft.modelCustom.trim() : draft.model
  // The default is no carry-over: every draft settles to it, so storing it
  // would make the banner claim a recall on every sheet.
  kept.provider = draft.provider === DEFAULT_PROVIDER ? '' : draft.provider || ''
  kept.skills = [...draft.skills]
  // Args with no skill are not a selection: the sheet hides the input the
  // moment no skill is ticked, so storing them would let an invisible field
  // be the sole reason the next sheet claims a carry-over.
  if (!kept.skills.length) kept.skillArgs = ''
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
  return REMEMBERED.some(field => field === 'provider'
    ? !isEmpty(draft.provider) && draft.provider !== DEFAULT_PROVIDER
    : !isEmpty(draft[field]))
}

// R4b: once the plan (a slice's arm-proposal) or history (the last attempt)
// has spoken, the remembered prefs may only say HOW to run — provider, skill
// and model.
// Everything else they carry was another task's, and a cwd borrowed from it
// would silently outrank nothing and aim this one elsewhere.
const HOW_ONLY = ['provider', 'model', 'skills', 'skillArgs']

/** A slice row's proposal (`GET /tasks/<id>/arm-proposal`) as an attempt. */
export function proposalAttempt(proposal) {
  if (!proposal) return null
  return {
    params: {
      worktree: proposal.branch ? { branch: proposal.branch, base: proposal.base || '' } : null,
      skill: proposal.skill ? { name: proposal.skill } : null,
    },
    verify_cmd: proposal.verify_cmd || null,
  }
}

function isEmpty(value) {
  return value === '' || value === false || value == null
    || (Array.isArray(value) && !value.length)
}

// A stored list is usable only as names: anything else is a store some other
// sheet wrote, and arming it would send the server a shape it refuses.
function usableStored(field, value, stored) {
  if (field === 'skills') {
    return Array.isArray(value) && value.length > 0
      && value.every(name => typeof name === 'string' && !!name)
  }
  // The sentinel names a slot in the form, not a model — refuse it on the way
  // in as well as on the way out, so a store written before `rememberArm`
  // resolved it cannot put the literal `__custom__` into the custom input.
  if (value === CUSTOM_MODEL) return false
  // Args are meaningful only under a skill — the sheet hides the input when
  // none is ticked, and an invisible field must never be the sole reason the
  // banner claims a carry-over.
  if (field === 'skillArgs' && !usableStored('skills', stored.skills, stored)) return false
  return typeof value === 'string' ? !!value : value === true
}

// A model id means something only to the CLI it was picked for: `opus` sent
// to kimi is a model it refuses at session/set_model.
function modelFits(draft, stored) {
  return (stored.provider || DEFAULT_PROVIDER) === (draft.provider || DEFAULT_PROVIDER)
}

// First non-empty source wins, per field. `useWorktree` follows the branch
// it gates, so a proposal's branch switches the worktree on with it.
function fillEmpty(draft, attempt) {
  const from = armDraft({ armed: attempt })
  for (const [field, value] of Object.entries(from)) {
    if (isEmpty(draft[field]) && !isEmpty(value)) draft[field] = value
  }
}

function fillFromStored(draft, task, fields) {
  const stored = read()
  if (!stored) return []
  const filled = []
  for (const field of fields) {
    // A filed task's directory is its project's repo (`projectCwd`), never the
    // last task's: that is how a task filed under repo B came to run in A.
    if (field === 'cwd' && task?.project_id != null) continue
    if (!isEmpty(draft[field])) continue
    if (field === 'model' && !modelFits(draft, stored)) continue
    const value = stored[field]
    if (usableStored(field, value, stored)) {
      draft[field] = Array.isArray(value) ? [...value] : value
      filled.push(field)
    }
  }
  return filled
}

/** The draft the sheet opens with, plus which of its fields came from the last arm.
 *
 * R4b, per field, first non-empty source wins:
 *   1. the task's pending attempt — the parameters its next run will actually
 *      spend; when present it wins outright, since re-seeding it from anything
 *      else would silently re-aim an attempt the operator only reopened to read;
 *   2. the slice row's arm-proposal;
 *   3. the last attempt's parameters;
 *   4. the remembered prefs — only skill/model once 2 or 3 spoke, and never
 *      branch/base (they are not remembered at all).
 */
export function prefilledArmDraft(task, { proposal = null, lastRun = null } = {}) {
  const draft = armDraft(task)
  if (task?.armed?.params) return { draft: withProvider(draft), recalled: false, recalledFields: [] }
  const higher = [proposalAttempt(proposal), lastRun].filter(Boolean)
  for (const attempt of higher) fillEmpty(draft, attempt)
  // A run that names no provider ran on claude (D1 of #140); its model is a
  // claude id, so the remembered provider must not re-aim it.
  if (lastRun && !draft.provider) draft.provider = DEFAULT_PROVIDER
  const recalledFields = fillFromStored(draft, task, higher.length ? HOW_ONLY : REMEMBERED)
  return { draft: withProvider(draft), recalled: recalledFields.length > 0, recalledFields }
}

function withProvider(draft) {
  draft.provider ||= DEFAULT_PROVIDER
  return draft
}

export function seededArmDraft(task) {
  return prefilledArmDraft(task)
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
  return withProvider(armDraft({ ...task, armed: null }))
}

export { KEY as ARM_PREFS_KEY, REMEMBERED as ARM_REMEMBERED_FIELDS }
