/**
 * SessionTraceView (conversation mode): the live prompt placeholder→anchor
 * handoff must render as exactly ONE user card, never a duplicate.
 *
 * Regression for the bug where a freshly-sent prompt showed twice: the hook
 * writes a PENDING `promptlive-<hash>` placeholder for instant feedback, then
 * the real `prompt-<uuid>` anchor lands and the backend retires the
 * placeholder. The timeline `treeNodes` reconcile dropped it by DB id, but the
 * conversation cards read the flat `session.spans` list, which `mergeLoadedSpans`
 * only ever appended to — so the retired placeholder lingered and rendered a
 * second USER card next to its anchor. `dropRetiredPromptPlaceholders` (run in
 * `reloadLiveTail`) converges that list to the DB window, the same way the
 * timeline already did.
 *
 * Data is injected via `/api/session-spans` with `is_test=true` so the trace
 * is invisible in the sessions list and the test is portable to any clean DB.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID, createHash } from 'node:crypto'

// Mirror of lib/trace/pending_spans.prompt_placeholder_id so the placeholder
// id matches what the backend retires on anchor arrival (sha1 of
// `${traceId}\x00${text[:512]}`, first 13 hex chars).
function promptPlaceholderId(traceId, text) {
  const key = `${traceId || ''}\x00${(text || '').trim().slice(0, 512)}`
  const digest = createHash('sha1').update(key, 'utf8').digest('hex').slice(0, 13)
  return `promptlive-${digest}`
}

const LIVE_TEXT = 'live handoff fixture prompt'

async function post(page, spans) {
  const res = await page.request.post('/api/session-spans', { data: spans })
  expect(res.ok()).toBeTruthy()
}

// One USER badge per user-prompt card in SessionConversationView.
function userBadges(page) {
  return page.getByText('USER', { exact: true })
}

test('live prompt placeholder is superseded by its anchor, not duplicated', async ({ page }) => {
  const traceId = randomUUID()
  const seedId = `prompt-${traceId.slice(0, 8)}`
  const phId = promptPlaceholderId(traceId, LIVE_TEXT)
  const anchorId = `prompt-${traceId.slice(24)}-live`

  // Seed: one settled prompt + the live PENDING placeholder (newest).
  await post(page, [
    {
      trace_id: traceId, span_id: seedId, parent_id: null, name: 'prompt',
      start_time: '2026-05-17T08:00:00.000000',
      attributes: { text: 'earlier settled prompt', is_test: true },
    },
    {
      trace_id: traceId, span_id: phId, parent_id: null, name: 'prompt',
      start_time: '2026-05-17T08:05:00.000000', status_code: 'PENDING',
      attributes: { text: LIVE_TEXT, live_placeholder: true, is_test: true },
    },
  ])

  // Track when the live poll's /map fetch actually carries the anchor. Without
  // this gate the final count==2 is ambiguous: seed+placeholder and seed+anchor
  // both total 2, so a poll that never fired would false-pass. Gating on the
  // anchor being fetched proves the handoff really happened before we assert.
  let anchorFetched = false
  page.on('response', async (resp) => {
    if (resp.request().method() !== 'GET') return
    if (!resp.url().includes(`/sessions/${traceId}/map`)) return
    try {
      const body = await resp.text()
      if (body.includes(anchorId)) anchorFetched = true
    } catch { /* response body unavailable — ignore */ }
  })

  await page.goto(`/trace/sessions/${traceId}`)
  // Conversation mode (default): wait for the seeded prompt to render.
  await expect(page.getByText('earlier settled prompt').first())
    .toBeVisible({ timeout: 10_000 })

  // Instant feedback: the placeholder shows as a card right away — seed + live
  // = 2 user cards, and the live text is present.
  await expect.poll(() => userBadges(page).count(), { timeout: 10_000 }).toBe(2)
  await expect(page.getByText(LIVE_TEXT).first()).toBeVisible()

  // The real anchor lands (same text) → backend retires the placeholder.
  await post(page, [
    {
      trace_id: traceId, span_id: anchorId, parent_id: null, name: 'prompt',
      start_time: '2026-05-17T08:05:00.000000', end_time: '2026-05-17T08:05:05.000000',
      status_code: 'OK', attributes: { text: LIVE_TEXT, is_test: true },
    },
  ])

  // Wait until the live poll (~4s) has fetched the anchor — proof the handoff
  // reached the client. Only then is the card count meaningful.
  await expect.poll(() => anchorFetched, { timeout: 15_000 }).toBeTruthy()

  // Card count must stay at 2 — the anchor REPLACES the placeholder. Before the
  // fix it climbed to 3 (duplicate). A short hold outlasts another poll tick so
  // a late-arriving duplicate is caught, not raced past.
  await expect.poll(() => userBadges(page).count(), { timeout: 5_000 }).toBe(2)
  await page.waitForTimeout(5_000)
  expect(await userBadges(page).count()).toBe(2)

  // The live prompt is still shown — superseded, not dropped.
  await expect(page.getByText(LIVE_TEXT).first()).toBeVisible()
})
