import api from '../../api'

// The fields a defect verb collects before it posts. `invalid` is the same
// rule the server enforces, stated where the operator can still act on it.

const positive = (noun) => (raw) => (/^\d+$/.test(String(raw).trim()) && Number(raw) > 0
  ? '' : `A ${noun} id is a positive whole number.`)

export const FORMS = {
  attribute: {
    invalid: raw => (raw ? '' : 'Pick the feature that caused this.'),
    hint: 'The feature whose work caused this bug.',
    send: (id, raw) => api.post(`/defects/${id}/attribute`, { feature_id: Number(raw) }),
    done: 'attributed',
  },
  close: {
    label: 'Close', placeholder: 'the test that would have caught this',
    invalid: raw => (raw.trim()
      ? '' : 'Name the check that would have caught this.'),
    hint: 'Name the test that would have caught it — then write it.',
    send: (id, raw) => api.post(`/defects/${id}/close`, { missing_check: raw.trim() }),
    done: 'closed',
  },
  decline: {
    label: 'Decline', placeholder: 'why — optional',
    invalid: () => '',
    hint: 'Not a defect. Restore puts it back.',
    send: (id, raw) => api.post(`/defects/${id}/decline`, { reason: raw.trim() || null }),
    done: 'declined',
  },
  merge: {
    invalid: positive('defect'),
    send: (id, raw) => api.post(`/defects/${id}/merge`, { into: Number(raw) }),
    done: 'folded into the keeper',
  },
  'fix-item': {
    label: 'Link', placeholder: 'or a task id, e.g. 142',
    invalid: positive('task'),
    hint: 'One task can fix several defects: its title and spec update to list every one linked to it.',
    send: (id, raw) => api.post(`/defects/${id}/fix-item`, { task_id: Number(raw.trim()) }),
    done: 'linked to its fix task',
  },
}

// A finished task cannot fix anything, so the picker offers only the ones
// still in play; any other id stays reachable by typing it.
const FINISHED = new Set(['done', 'archived'])
const LINKABLE_MAX = 200

export async function linkableTasks(featureId) {
  if (!featureId) return []
  const body = await api.get(`/tasks?feature_id=${featureId}&size=${LINKABLE_MAX}`)
  return (body.items || []).filter(t => !FINISHED.has(t.status))
}
