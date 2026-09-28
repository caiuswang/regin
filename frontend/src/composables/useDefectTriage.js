import { computed, ref } from 'vue'
import api from '../api'

// The triage queue's four terminal verbs, and the cursor that walks them.
//
// A defect leaves the queue by exactly one of them, each one POST the server
// either takes or refuses; the queue is re-read afterwards rather than patched
// in place, so membership stays the server's predicate.
//
// Nothing here proposes a feature. Accept posts the feature the operator
// blamed in the picker, and the picker starts empty on every card: a wrong
// parent inflates one feature's rounds and exonerates another.

export const VERBS = {
  A: {
    key: 'accept',
    label: 'Accept',
    path: 'attribute',
    body: (_v, featureId) => ({ feature_id: Number(featureId) }),
    invalid: (_v, featureId) => (featureId
      ? '' : 'Blame a feature first — none is preselected.'),
    immediate: true,
    done: (id) => `#${id} attributed.`,
  },
  D: {
    key: 'duplicate',
    label: 'Dup',
    prompt: 'the defect to KEEP — this one folds into it',
    path: 'merge',
    body: (v) => ({ into: Number(v) }),
    invalid: (v) => (/^\d+$/.test(String(v).trim()) && Number(v) > 0
      ? '' : 'A defect id is a positive whole number.'),
    done: (id) => `#${id} folded into the keeper.`,
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

export const VERB_ORDER = ['A', 'D', 'X', 'S']

// The queue is read under no project scope: a defect in it has no feature and
// so no project, and the server drops a project scope on `scope=triage` anyway.
export function useDefectTriage() {
  const items = ref([])
  const total = ref(0)
  const loading = ref(true)
  const busy = ref(false)
  const error = ref('')
  const refusal = ref('')
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

  function cancel() {
    pending.value = ''
    refusal.value = ''
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
        refusal.value = body.msg || 'the ledger refused, without saying why'
        return false
      }
      return true
    } catch (e) {
      refusal.value = e?.message || String(e)
      return false
    } finally {
      busy.value = false
    }
  }

  async function submit(chord, value = '') {
    const verb = VERBS[chord]
    const row = focused.value
    if (!verb || !row || busy.value) return undefined
    const bad = verb.invalid(value, blamed.value)
    if (bad) {
      refusal.value = bad
      return undefined
    }
    if (!(await post(row, verb, value))) return undefined
    cancel()
    blamed.value = ''
    await load()
    return verb.done(row.id)
  }

  /** Begin a verb: run it outright when it takes no field, else open one. */
  function begin(chord) {
    const verb = VERBS[chord]
    if (!verb || !focused.value) return undefined
    refusal.value = ''
    if (verb.immediate) return submit(chord, '')
    pending.value = chord
    return undefined
  }

  /** Mobile's primary verb: accept, then author the fix task for it. */
  async function acceptAndFix() {
    const row = focused.value
    if (!row || busy.value) return undefined
    const bad = VERBS.A.invalid('', blamed.value)
    if (bad) {
      refusal.value = bad
      return undefined
    }
    if (!(await post(row, VERBS.A, ''))) return undefined
    const fix = { path: 'fix-item', body: () => ({ create: true }) }
    const created = await post(row, fix, '')
    const why = refusal.value
    cancel()
    blamed.value = ''
    await load()
    if (!created) {
      refusal.value = `#${row.id} attributed, but the fix task was refused — ${why}`
      return undefined
    }
    return `#${row.id} attributed; fix task created.`
  }

  return {
    items, total, loading, busy, error, refusal, cursor, pending, blamed,
    focused, load, move, focus, begin, submit, cancel, acceptAndFix,
  }
}
