import { ref } from 'vue'
import api from '../api'
import { useFlash } from './useFlash.js'

/**
 * useTaskExec — the three control routes a task row offers (`run`, `stop`,
 * `interrupt`) plus the liveness a `running` row cannot answer on its own.
 *
 * A refusal is kept per task rather than flashed away: a `409` from `run`
 * carries the ids of the dependencies still blocking it, and those belong
 * next to the button that was pressed, as links, for as long as they are true.
 *
 * `syncLiveness` merges the row's claim with the server's `live` flag, which
 * is the whole point of `/runs`: `status == 'running'` outlives the process
 * that wrote it, so a runner killed with its server would otherwise render as
 * a live agent forever. It is called after a load and after a mutation — never
 * on a timer.
 *
 * The map holds ANSWERS, and only about the tasks it was asked for: a missing
 * entry means "not asked yet", never "dead". Both distinctions are load-bearing
 * — the grouped list calls this once per group, so a whole-map replacement lets
 * a group holding no running row erase another group's answers, and a row that
 * renders before its answer arrives would accuse a live agent of being gone.
 */
export function useTaskExec(reload) {
  const { flash } = useFlash()
  const refusals = ref({})
  const liveness = ref({})
  const busyId = ref(null)

  function clearRefusal(id) {
    if (!(id in refusals.value)) return
    const next = { ...refusals.value }
    delete next[id]
    refusals.value = next
  }

  function refuse(id, result) {
    refusals.value = {
      ...refusals.value,
      [id]: {
        message: result.msg || 'The server refused this.',
        blocking: result.body?.blocking || [],
      },
    }
  }

  async function answerFor(id) {
    try {
      const data = await api.get(`/tasks/${id}/runs`)
      return { live: !!(data.runs || [])[0]?.live }
    } catch {
      // A row whose history cannot be read is left unannotated rather than
      // claimed dead: not knowing is not evidence the process is gone.
      return null
    }
  }

  async function syncLiveness(tasks) {
    const asked = tasks || []
    const answers = await Promise.all(
      asked.filter(t => t.status === 'running')
        .map(async t => [t.id, await answerFor(t.id)]))
    const next = { ...liveness.value }
    for (const t of asked) delete next[t.id]
    for (const [id, answer] of answers) {
      if (answer) next[id] = answer
    }
    liveness.value = next
  }

  async function control(task, action) {
    busyId.value = task.id
    const result = await api.post(`/tasks/${task.id}/${action}`)
    busyId.value = null
    if (result.ok === false) {
      refuse(task.id, result)
      return false
    }
    clearRefusal(task.id)
    const link = { to: `/tasks/${task.id}`, label: `#${task.id}` }
    if (action === 'run') {
      const attempt = result.run?.attempt
      const traceId = result.run?.trace_id
      flash(attempt ? `Attempt ${attempt} of #${task.id} started` : `#${task.id} started`,
        'success', { link: { to: traceId ? `/trace/sessions/${traceId}` : `/tasks/${task.id}`,
                             label: 'Watch' } })
    } else if (result.delivered === false) {
      flash(result.detail || `The run did not take the ${action}`, 'error', { link })
    } else flash(`${action === 'stop' ? 'Stopped' : 'Interrupted'} #${task.id}`, 'success', { link })
    await reload()
    return true
  }

  return { refusals, liveness, busyId, control, clearRefusal, syncLiveness }
}
