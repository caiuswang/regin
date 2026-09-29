import api from '../../api'

// The fields a defect verb collects before it posts. `invalid` is the same
// rule the server enforces, stated where the operator can still act on it.

function idList(raw) {
  const tokens = raw.split(/[\s,]+/).filter(Boolean)
  if (!tokens.length || !tokens.every(t => /^\d+$/.test(t) && Number(t) > 0)) return null
  return [...new Set(tokens.map(Number))]
}

const positive = (noun) => (raw) => (/^\d+$/.test(String(raw).trim()) && Number(raw) > 0
  ? '' : `A ${noun} id is a positive whole number.`)

export const FORMS = {
  edit: {
    label: 'Save', placeholder: 'what went wrong, in the reporter’s words',
    multiline: true,
    initial: d => d.symptom,
    invalid: raw => (raw.trim() ? '' : 'A defect needs a symptom.'),
    hint: 'The wording is the dedup key too: a rewrite that matches another defect of the same feature is refused, and merge is the remedy. Paste a screenshot to attach it.',
    send: (id, raw) => api.patch(`/defects/${id}`, { symptom: raw.trim() }),
    done: 'edited',
  },
  paths: {
    label: 'Save paths', placeholder: 'one path per line',
    multiline: true,
    initial: d => (d.suspect_paths || []).join('\n'),
    invalid: () => '',
    hint: 'Where it was seen, not who caused it. Replaces the list; a blank list clears it.',
    send: (id, raw) => api.patch(`/defects/${id}`, { suspect_paths: raw.split('\n') }),
    done: 'has new suspect paths',
  },
  absorb: {
    label: 'Absorb into', placeholder: 'duplicate ids, e.g. 18, 19',
    invalid: raw => (idList(raw)
      ? '' : 'Defect ids are positive whole numbers, separated by commas or spaces.'),
    hint: 'Every one named folds into this defect and is deleted, all or none. This one keeps its wording and round; their suspect paths, screenshots and fix task move here. Refused while their fix tasks differ, or when this one is closed and any of them is open.',
    send: (id, raw) => api.post(`/defects/${id}/absorb`, { duplicates: idList(raw) }),
    done: 'absorbed its duplicates',
  },
  attribute: {
    invalid: raw => (raw ? '' : 'Pick the feature that caused this.'),
    hint: 'The feature that CAUSED this, which is not necessarily the one the reporting session was working on. Naming a different one re-points the blame at that feature’s current last event, so the escape verdict follows the blame.',
    send: (id, raw) => api.post(`/defects/${id}/attribute`, { feature_id: Number(raw) }),
    done: 'attributed',
  },
  close: {
    label: 'Close', placeholder: 'the test that would have caught this',
    invalid: raw => (raw.trim()
      ? '' : 'Name the check that would have caught this. It is the only field here that changes future behaviour.'),
    hint: 'Name a test, then write it. This is the only field here that changes future behaviour.',
    send: (id, raw) => api.post(`/defects/${id}/close`, { missing_check: raw.trim() }),
    done: 'closed',
  },
  decline: {
    label: 'Decline', placeholder: 'why — optional',
    invalid: () => '',
    hint: 'Not a defect. It leaves triage closed with no missing check; Restore puts it back.',
    send: (id, raw) => api.post(`/defects/${id}/decline`, { reason: raw.trim() || null }),
    done: 'declined',
  },
  merge: {
    invalid: positive('defect'),
    send: (id, raw) => api.post(`/defects/${id}/merge`, { into: Number(raw) }),
    done: 'folded into the keeper',
  },
  'fix-item': {
    label: 'Link task', placeholder: 'task id, e.g. 142',
    invalid: positive('task'),
    hint: 'An item that already exists. Last writer wins — re-pointing is how a wrong one is corrected, and nothing is counted from this pointer.',
    send: (id, raw) => api.post(`/defects/${id}/fix-item`, { task_id: Number(raw.trim()) }),
    done: 'pointed at its fix task',
  },
}
