import { computed, ref } from 'vue'
import api from '../api'
import { useConfirm } from './useConfirm'
import { refusalOf, runRemedy } from './useDefectRemedy'
import { useFlash } from './useFlash'

// The triage queue's terminal verbs, and the cursor that walks them.
//
// A defect leaves the queue by one POST the server either takes or refuses;
// the queue is re-read afterwards rather than patched in place, so membership
// stays the server's predicate.
//
// Nothing here proposes a feature. Accept posts the feature the operator
// blamed in the picker, and the picker starts empty on every card: a wrong
// parent inflates one feature's rounds and exonerates another.
//
// Accept means one thing on every surface (D6): attribute AND author the fix
// task, because most accepts need one. Accept only (⇧A) attributes and stops.

const needsFeature = (_v, featureId) => (featureId
  ? '' : 'Blame a feature first — none is preselected.')

export const VERBS = {
  A: {
    key: 'accept',
    label: 'Accept',
    path: 'attribute',
    body: (_v, featureId) => ({ feature_id: Number(featureId) }),
    invalid: needsFeature,
    immediate: true,
    withFix: true,
    done: (id) => `#${id} attributed; fix task created.`,
  },
  'Shift+A': {
    key: 'accept-only',
    label: 'Accept only',
    kbd: '⇧A',
    path: 'attribute',
    body: (_v, featureId) => ({ feature_id: Number(featureId) }),
    invalid: needsFeature,
    immediate: true,
    done: (id) => `#${id} attributed.`,
  },
  D: {
    key: 'duplicate',
    label: 'Dup',
    // The keeper is chosen in the merge picker, which previews it first.
    picker: true,
    path: 'merge',
    body: (v) => ({ into: Number(v) }),
    invalid: (v) => (/^\d+$/.test(String(v).trim()) && Number(v) > 0
      ? '' : 'Pick the defect to keep.'),
    done: (id, v) => `#${id} folded into #${Number(v)}.`,
    link: (v) => ({ to: `/defects/${Number(v)}`, label: `Open #${Number(v)}` }),
  },
  X: {
    key: 'decline',
    label: 'Decline',
    prompt: 'why — optional',
    path: 'decline',
    body: (v) => ({ reason: String(v).trim() || null }),
    // The confirm step is the prompt's submit: a decline closes the report,
    // so it is never one keystroke.
    invalid: () => '',
    optional: true,
    done: (id) => `#${id} declined.`,
  },
  S: {
    key: 'snooze',
    label: 'Snooze',
    // A snooze has no deadline to type: it ends when something writes to the
    // defect, which is an event.
    path: 'snooze',
    body: () => ({}),
    invalid: () => '',
    immediate: true,
    done: (id) => `#${id} snoozed until something touches it.`,
  },
}

export const VERB_ORDER = ['A', 'Shift+A', 'D', 'X', 'S']

/** The chord a keydown names, or '' — Shift only matters on A. */
export function chordOf(event) {
  if (event.key.length !== 1) return ''
  const key = event.key.toUpperCase()
  if (key === 'A' && event.shiftKey) return 'Shift+A'
  return VERBS[key] ? key : ''
}

const FIX = { path: 'fix-item', body: () => ({ create: true }) }

// The queue is read under no project scope: a defect in it has no feature and
// so no project, and the server drops a project scope on `scope=triage` anyway.
export function useDefectTriage() {
  const { flash } = useFlash()
  const items = ref([])
  const total = ref(0)
  const loading = ref(true)
  const busy = ref(false)
  const error = ref('')
  // `{ msg, remedy }` or null — the server's sentence, and the verb it names.
  const refusal = ref(null)
  // A refusal about a defect that has already LEFT the queue (Accept whose
  // fix task was refused): shown above the cards, never on whichever card the
  // cursor lands on next.
  const notice = ref(null)
  const { confirm } = useConfirm()
  const cursor = ref(0)
  // Which verb is collecting a field, or ''. Always on the focused row — a
  // prompt that outlived the cursor would write to a defect nobody is on.
  const pending = ref('')
  // The feature blamed on the focused card. Cleared on every move.
  const blamed = ref('')
  let issued = 0

  const focused = computed(() => items.value[cursor.value] || null)

  async function load() {
    const mine = ++issued
    error.value = ''
    try {
      const params = new URLSearchParams({ scope: 'triage', sort: 'opened:desc', size: '50' })
      const body = await api.get(`/defects?${params}`)
      if (mine !== issued) return
      items.value = body.items || []
      total.value = body.pagination?.total ?? items.value.length
      // Clamped rather than reset: acting on the last row should leave the
      // operator on the new last row, not back at the top.
      cursor.value = Math.min(cursor.value, Math.max(0, items.value.length - 1))
    } catch (e) {
      if (mine === issued) error.value = e?.message || String(e)
    } finally {
      if (mine === issued) loading.value = false
    }
  }

  /**
   * Put one defect in the loaded queue when it is past the page that was
   * read — a link to `?triage=<id>` names a row, not a page. Only a row the
   * server says is still queued is added. Resolves to its index, or -1.
   */
  async function reach(id) {
    const at = items.value.findIndex(d => String(d.id) === String(id))
    if (at >= 0) return at
    try {
      const body = await api.get(`/defects/${id}`)
      if (body?.defect?.queue_position !== 'queue') return -1
      items.value = [...items.value, body.defect]
      return items.value.length - 1
    } catch {
      return -1
    }
  }

  function cancel() {
    pending.value = ''
    refusal.value = null
  }

  function focus(index) {
    // Only a MOVE closes an open prompt: the submit button inside the prompt
    // is clicked through the card, and cancelling there unmounted the form
    // out from under its own submit event.
    if (index === cursor.value || index < 0 || index >= items.value.length) return
    cancel()
    blamed.value = ''
    cursor.value = index
  }

  function move(delta) {
    focus(Math.min(Math.max(0, cursor.value + delta), items.value.length - 1))
  }

  async function post(row, verb, value) {
    busy.value = true
    try {
      // `api.post` answers an HTTP refusal with `{ok:false}` but REJECTS when
      // the fetch itself fails; without the finally that left `busy` true and
      // every later verb silently dead.
      const body = await api.post(`/defects/${row.id}/${verb.path}`, verb.body(value, blamed.value))
      if (body?.ok === false) {
        refusal.value = refusalOf(body)
        return false
      }
      return true
    } catch (e) {
      refusal.value = refusalOf(e?.message || String(e))
      return false
    } finally {
      busy.value = false
    }
  }

  async function settle(message) {
    cancel()
    blamed.value = ''
    await load()
    return message
  }

  async function acceptAndFix(row) {
    if (!(await post(row, VERBS.A, ''))) return undefined
    if (!(await post(row, FIX, ''))) {
      const why = refusal.value
      await settle('')
      notice.value = { msg: `#${row.id} attributed, but the fix task was refused — ${why.msg}`,
                       remedy: why.remedy }
      return undefined
    }
    return settle(VERBS.A.done(row.id))
  }

  async function submit(chord, value = '') {
    const verb = VERBS[chord]
    const row = focused.value
    if (!verb || !row || busy.value) return undefined
    notice.value = null
    const bad = verb.invalid(value, blamed.value)
    if (bad) {
      refusal.value = refusalOf(bad)
      return undefined
    }
    if (verb.withFix) return acceptAndFix(row)
    if (!(await post(row, verb, value))) return undefined
    const message = verb.done(row.id, value)
    if (verb.link) flash(message, 'success', { link: verb.link(value) })
    return settle(message)
  }

  /** Begin a verb: run it outright when it takes no field, else open one. */
  function begin(chord) {
    const verb = VERBS[chord]
    if (!verb || !focused.value) return undefined
    refusal.value = null
    notice.value = null
    if (verb.immediate) return submit(chord, '')
    pending.value = chord
    return undefined
  }

  /** Run the verb a refusal named. Resolves to a flash message, or undefined. */
  async function remedy(r, where = refusal) {
    busy.value = true
    let result
    try {
      result = await runRemedy(r, { confirm })
    } catch (e) {
      result = { ok: false, refusal: refusalOf(e?.message || String(e)) }
    } finally {
      busy.value = false
    }
    if (result.reveal) return undefined
    if (!result.ok) {
      // A cancelled confirm has no refusal; the one on screen stays up.
      if (result.refusal) where.value = result.refusal
      return undefined
    }
    notice.value = null
    return settle(result.message)
  }

  return {
    items, total, loading, busy, error, refusal, notice, cursor, pending, blamed,
    focused, load, reach, move, focus, begin, submit, cancel, remedy,
  }
}
