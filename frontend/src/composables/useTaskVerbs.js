import api from '../api'
import { useConfirm } from './useConfirm.js'
import { useFlash } from './useFlash.js'
import { useTaskExec } from './useTaskExec.js'

/**
 * useTaskVerbs — every R4a task verb behind one `act(verb, task)`.
 *
 * Which verbs a row offers is the server's (`task.action`, `task.menu`,
 * `lib/tasks/verbs.py`); this only carries each one out, through the same
 * route the API exposes. A refusal lands in `refusals` keyed by task, so the
 * surface renders it beside the control through `TaskRefusal` rather than
 * flashing it away.
 *
 * `openSheet(kind, task)` is the host's: arm, edit and deps are sheets the
 * host owns.
 */
export const VERB_LABELS = {
  arm: 'Re-arm',
  disarm: 'Disarm',
  edit: 'Edit',
  deps: 'Dependencies',
  mark_done: 'Mark done',
  reopen: 'Reopen',
  archive: 'Archive',
  restore: 'Restore',
  delete: 'Delete',
  interrupt: 'Interrupt',
}

const SHEETS = new Set(['arm', 'edit', 'deps'])
const RUNS = { run: 'run', retry: 'run', interrupt: 'interrupt' }
const STATUS_MOVES = { reopen: 'idea', archive: 'archived', restore: 'idea' }

function name(task) {
  return `#${task.id} “${task.title}”`
}

export function useTaskVerbs(reload, { openSheet = () => {} } = {}) {
  const exec = useTaskExec(reload)
  const { confirm } = useConfirm()
  const { flash } = useFlash()

  async function write(task, call, done) {
    exec.busyId.value = task.id
    const result = await call()
    exec.busyId.value = null
    if (result.ok === false) {
      exec.refusals.value = {
        ...exec.refusals.value,
        [task.id]: { message: result.msg || 'The server refused this.',
                     blocking: result.body?.blocking || [] },
      }
      return false
    }
    exec.clearRefusal(task.id)
    if (done) flash(done, 'success')
    await reload()
    return true
  }

  async function stop(task) {
    const ok = await confirm('Stop run',
      `Stop the run of ${name(task)}? The agent is interrupted and its session closed.`, true)
    if (ok) await exec.control(task, 'stop')
  }

  async function markDone(task) {
    const open = task.fixes_open_defects || []
    const note = open.length
      ? `\n\nIt is the fix task of ${open.length === 1 ? 'defect' : 'defects'} `
        + `${open.map(d => `#${d}`).join(', ')}, still open. Marking it done does not close `
        + `${open.length === 1 ? 'it' : 'them'} — closing needs the check that would have caught it.`
      : ''
    const ok = await confirm('Mark done',
      `Mark ${name(task)} done? Anything armed behind it starts.${note}`, false,
      { links: open.map(d => ({ to: `/defects/${d}`, label: `defect #${d}` })) })
    if (!ok) return
    await write(task, () => api.patch(`/tasks/${task.id}`, { status: 'done' }), `#${task.id} done`)
  }

  async function remove(task) {
    const ok = await confirm('Delete task',
      `Delete ${name(task)} and its run history, images and links? `
      + 'This cannot be undone — archive it instead to keep it.', true)
    if (ok) await write(task, () => api.del(`/tasks/${task.id}`), 'Task deleted')
  }

  async function act(verb, task) {
    if (SHEETS.has(verb)) return openSheet(verb, task)
    if (RUNS[verb]) return exec.control(task, RUNS[verb])
    if (STATUS_MOVES[verb]) {
      return write(task, () => api.patch(`/tasks/${task.id}`, { status: STATUS_MOVES[verb] }))
    }
    if (verb === 'stop') return stop(task)
    if (verb === 'disarm') {
      return write(task, () => api.post(`/tasks/${task.id}/disarm`), `#${task.id} disarmed`)
    }
    if (verb === 'mark_done') return markDone(task)
    if (verb === 'delete') return remove(task)
    return undefined
  }

  return { ...exec, act }
}
