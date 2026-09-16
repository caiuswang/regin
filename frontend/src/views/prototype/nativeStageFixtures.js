// PROTOTYPE — throwaway (CAI-269). Do not ship.
//
// Two features side by side: one Linear key whose stage can lag, one native
// key whose `issue_cache` row is rewritten by the same transaction that
// changes the fact (CAI-267). Shapes match what the API actually returns —
// `/api/features/<key>` (stage + stage_synced_at), `/api/features` rows, and
// `links.serialize()` — plus the one candidate new field, `stage_live`.

export const LINEAR = {
  key: 'CAI-257',
  title: 'Feature ledger on the web',
  stage: {
    stage: 'shipped',
    blurb: 'built and closed; no bugs blamed on it since',
    rounds: 1,
    open_defects: 0,
    escapes: 0,
  },
  stage_synced_at: '2026-09-14 09:12:04',
  stage_live: false,
  sessions: 11,
  cost_usd: 141.62,
  defects: { total: 2, open: 0, escapes: 0 },
  last_activity: '2026-09-13 22:40:11',
}

export const NATIVE = {
  key: 'regin:42',
  title: 'Feature ledger without Linear',
  stage: {
    stage: 'building',
    blurb: 'a session has been attributed; nothing has shipped',
    rounds: 2,
    open_defects: 1,
    escapes: 0,
  },
  // True, and written 90 seconds ago by the child-event recompute — not by a
  // human clicking anything.
  stage_synced_at: '2026-09-14 12:22:37',
  stage_live: true,
  sessions: 4,
  cost_usd: 38.9,
  defects: { total: 1, open: 1, escapes: 0 },
  last_activity: '2026-09-14 12:22:37',
}

// A third row for the index, so `stage not fetched` is visible beside both.
export const UNFETCHED = {
  key: 'CAI-999',
  title: null,
  stage: null,
  stage_synced_at: null,
  stage_live: false,
  sessions: 0,
  cost_usd: null,
  defects: { total: 3, open: 2, escapes: 1 },
  last_activity: '2026-09-02 08:11:00',
}

// `links.serialize()` output. The second one is the case CAI-269 asks about:
// some *other* task pasted `regin:42`, so the native key arrives on a card
// built for a third-party issue — internal URL, external-link affordance.
export const LINKS = [
  {
    id: 7,
    provider: 'linear',
    external_key: 'CAI-257',
    url: 'https://linear.app/caiuswang/issue/CAI-257',
    issue: {
      title: 'Feature ledger on the web',
      state: 'Done',
      state_category: 'completed',
      assignee: 'caius wang',
      labels: ['feature'],
      extra: { project: 'regin' },
    },
    stage: LINEAR.stage,
    synced_at: LINEAR.stage_synced_at,
    stage_live: false,
    sync_error: null,
  },
  {
    id: 8,
    provider: 'regin',
    external_key: 'regin:42',
    url: '/tasks/42',
    issue: {
      title: 'Feature ledger without Linear',
      state: 'running',
      state_category: 'started',
      assignee: 'caius wang',
      labels: [],
      extra: { project: 'regin' },
    },
    stage: NATIVE.stage,
    synced_at: NATIVE.stage_synced_at,
    stage_live: true,
    sync_error: null,
  },
]

export const VARIANTS = [
  {
    key: 'A',
    name: 'unchanged',
    blurb: 'Native keeps the age stamp and the Refresh button. No new field, '
      + 'no new rule — the timestamp is true and the refresh really does '
      + 're-read. The button just can never change anything.',
    keysOn: 'nothing — no payload change, no branch anywhere',
  },
  {
    key: 'B',
    name: 'live, no refresh',
    blurb: 'The age stamp is replaced by the word `live`, and the Refresh '
      + 'button is gone. The absence is stated rather than left as a hole.',
    keysOn: 'stage_live: true — beside the stage, like stage_synced_at',
  },
  {
    key: 'C',
    name: 'silent',
    blurb: 'Neither freshness nor a Refresh control. A stage with nothing '
      + 'beside it is a stage nobody has to date.',
    keysOn: 'stage_live: true — same field, less chrome',
  },
  {
    key: 'D',
    name: 'stamp kept, button gone',
    blurb: 'The timestamp stays (it is a true fact about the row) but the '
      + 'control that can only rewrite it to now is removed.',
    keysOn: 'stage_live: true, read only to decide the control',
  },
]
