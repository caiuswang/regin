import { computed, ref } from 'vue'
import api from '../api'

// The triage queue's four terminal verbs, and the cursor that walks them.
//
// A defect leaves the queue by exactly one of them. There is no fifth exit and
// no partial state: every verb is one POST the server either takes or refuses,
// and the queue is re-read afterwards rather than patched in place — the
// predicate that decides membership is the server's, and re-reading is how it
// stays the server's.
//
// Nothing here proposes a feature. `accept` collects an id the operator types;
// the defect's suspect paths, its reporting session and its cost are never
// read to fill that field, because a wrong parent inflates one feature's
// rounds and exonerates another.

export const VERBS = {
  1: {
    key: 'accept',
    label: 'Accept',
    // The one field this whole surface exists to collect, and the reason it
    // starts empty every time.
    prompt: 'feature id — the feature that CAUSED this',
    path: 'attribute',
    body: (v) => ({ feature_id: Number(v) }),
    invalid: (v) => (/^\d+$/.test(v.trim()) && Number(v) > 0
      ? '' : 'A feature id is a positive whole number.'),
    done: (id) => `#${id} attributed.`,
  },
  2: {
    key: 'duplicate',
    label: 'Mark duplicate',
    prompt: 'the defect to KEEP — this one folds into it',
    path: 'merge',
    body: (v) => ({ into: Number(v) }),
    invalid: (v) => (/^\d+$/.test(v.trim()) && Number(v) > 0
      ? '' : 'A defect id is a positive whole number.'),
    done: (id) => `#${id} folded into the keeper.`,
  },
  3: {
    key: 'decline',
    label: 'Decline',
    prompt: 'why — optional',
    path: 'decline',
    body: (v) => ({ reason: v.trim() || null }),
    // The only verb whose field may be empty: a report that was never a bug
    // has no check to name and owes no explanation.
    invalid: () => '',
    optional: true,
    done: (id) => `#${id} declined.`,
  },
  H: {
    key: 'snooze',
    label: 'Snooze',
    // No field at all. A snooze has no deadline to type: it ends when
    // something writes to the defect, which is an event.
    path: 'snooze',
    body: () => ({}),
    // Every verb carries an `invalid`, including the one with no field: the
    // writer calls it unconditionally, and a table with a hole in it is a
    // TypeError that swallows the whole verb.
    invalid: () => '',
    immediate: true,
    done: (id) => `#${id} snoozed until something touches it.`,
  },
}

export function useDefectTriage() {
  const items = ref([])
  const total = ref(0)
  const loading = ref(true)
  const busy = ref(false)
  const error = ref('')
  const refusal = ref('')
  const cursor = ref(0)
  // Which verb is collecting a field, or ''. One at a time and always on the
  // focused row — a prompt that outlived the cursor would write to a defect
  // the operator is no longer looking at.
  const pending = ref('')
  // Compared, not timed: two reads can be in flight and the one that answers
  // last must not win if it is not the one that was asked for.
  let issued = 0

  const focused = computed(() => items.value[cursor.value] || null)

  async function load() {
    const mine = ++issued
    error.value = ''
    try {
      // `pagination.total` and the rows come from one statement in one
      // session, so the count rendered beside the queue is the queue.
      const body = await api.get('/defects?scope=triage&sort=opened:asc&size=50')
      if (mine !== issued) return
      items.value = body.items || []
      total.value = body.pagination?.total ?? items.value.length
      // The cursor is a position in a list that just changed under it. Clamped
      // rather than reset: acting on the last row should leave the operator on
      // the new last row, not back at the top.
      cursor.value = Math.min(cursor.value, Math.max(0, items.value.length - 1))
    } catch (e) {
      if (mine === issued) error.value = e?.message || String(e)
    } finally {
      if (mine === issued) loading.value = false
    }
  }

  function move(delta) {
    if (!items.value.length) return
    cancel()
    cursor.value = Math.min(Math.max(0, cursor.value + delta),
                            items.value.length - 1)
  }

  function focus(index) {
    // Only a MOVE closes an open prompt. Clicking the card you are already on
    // is how the submit button inside the prompt gets clicked — the click
    // bubbles to the card — and cancelling there unmounted the form out from
    // under its own submit event ("Form submission canceled because the form
    // is not connected").
    if (index === cursor.value) return
    cancel()
    cursor.value = index
  }

  function cancel() {
    pending.value = ''
    refusal.value = ''
  }

  /** Begin a verb: run it outright when it takes no field, else open one. */
  function begin(chord) {
    const verb = VERBS[chord]
    if (!verb || !focused.value) return
    refusal.value = ''
    if (verb.immediate) return submit(chord, '')
    pending.value = chord
    return undefined
  }

  async function submit(chord, value) {
    const verb = VERBS[chord]
    const row = focused.value
    if (!verb || !row || busy.value) return
    // The field's own live check is in the card, where the operator can still
    // act on it. This is the same rule stated at the writer, so a submit that
    // bypassed the card — Enter on an empty field — cannot post a NaN id.
    const bad = verb.invalid(value)
    if (bad) {
      refusal.value = bad
      return
    }
    busy.value = true
    let body
    try {
      // `api.post` answers an HTTP refusal with `{ok:false}` but REJECTS when
      // the fetch itself fails — offline, reset, aborted. Without the finally
      // that rejection left `busy` true for good, and every later verb hit the
      // `busy` guard and returned silently: a dead queue with no error on it.
      body = await api.post(`/defects/${row.id}/${verb.path}`, verb.body(value))
    } catch (e) {
      refusal.value = e?.message || String(e)
      return
    } finally {
      busy.value = false
    }
    if (body?.ok === false) {
      refusal.value = body.msg || 'the ledger refused, without saying why'
      return
    }
    cancel()
    await load()
    return verb.done(row.id)
  }

  return {
    items, total, loading, busy, error, refusal, cursor, pending,
    focused, load, move, focus, begin, submit, cancel,
  }
}
