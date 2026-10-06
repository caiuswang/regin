/**
 * Close/Delete on the sessions list must warn that a live session will be
 * stopped first — and must say which tier is going to be stopped, because
 * for a tmux pane the consequence is a keystroke into the operator's own
 * terminal.
 *
 * Both cases end on Cancel: confirming would really end a live session.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'
import { API_BASE as API } from './helpers/api-base.js'
import { TEST_USER } from '../e2e-env.js'

// The unreachable case owns its row: a fresh trace with no bridge pane is
// non-ended and unreachable by construction, where a host session picked off
// the shared list is whatever the rest of the run left behind.
const ownTraceId = randomUUID()

// Server timestamps are naive local time; a recent one keeps the row inside
// the list's default window and off the stale path.
const localNow = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60_000)
  .toISOString().slice(0, 19)

test.beforeAll(async ({ request }) => {
  const { token } = await (await request.post(
    `${API}/api/auth/login`, { data: TEST_USER })).json()
  const seeded = await request.post(`${API}/api/session-spans`, {
    headers: { Authorization: `Bearer ${token}` },
    data: [{
      trace_id: ownTraceId, span_id: `prompt-${ownTraceId.slice(0, 8)}`,
      parent_id: null, name: 'prompt',
      start_time: localNow(), duration_ms: 0,
      attributes: { text: 'a session nothing can reach', is_test: true },
    }],
  })
  expect(seeded.ok()).toBeTruthy()
})


// `page.request` is a separate context from the app's fetch — it does not
// pick up the token the fixture puts in localStorage, so it needs its own.
async function apiHeaders(page) {
  const res = await page.request.post(`${API}/api/auth/login`, {
    data: { username: 'claude-admin', password: 'claude-admin-2026' },
  })
  const { token } = await res.json()
  return { Authorization: `Bearer ${token}` }
}

// Which traces are live changes run to run, so the live case is resolved from
// the API rather than hardcoded. Only a non-ended row renders Close at all
// (SessionListRow `v-if="s.status !== 'ended'"`): `wantLive` true picks one
// regin can still reach, false one it cannot.
async function findRow(page, wantLive) {
  const headers = await apiHeaders(page)
  const res = await page.request.get(`${API}/api/sessions?limit=60`, { headers })
  const body = await res.json()
  for (const s of body.sessions || []) {
    if (s.status === 'ended') continue
    const probe = await page.request.get(
      `${API}/api/sessions/${s.trace_id}/live-state`, { headers })
    const state = await probe.json()
    const live = Boolean(state.tier && state.live)
    if (live === wantLive) return { ...s, ...state }
  }
  return null
}

// The list filters are component state, not URL params, so a row is reached
// by its own markup: the copy-id control is the one per-row element that
// carries the full trace id. The list header is sticky and overlaps a row
// scrolled under it, hence the forced click. ConfirmDialog carries no ARIA
// role, so the overlay is addressed by its own markup too.
const DIALOG = '.fixed.inset-0.z-50'

async function clickRowClose(page, traceId) {
  const row = page.locator('.srow')
    .filter({ has: page.locator(`[aria-label="Copy session id ${traceId}"]`) })
  await expect(row).toHaveCount(1)
  const button = row.getByRole('button', { name: 'Close', exact: true })
  await button.scrollIntoViewIfNeeded()
  await button.click({ force: true })
  return page.locator(DIALOG).first()
}

test('close on a live session names the tier and the shutdown', async ({ page }) => {
  const live = await findRow(page, true)
  test.skip(!live, 'no reachable live session on this host right now')

  await page.goto('/trace/sessions')
  await page.waitForSelector('.srow', { timeout: 15000 })
  const dialog = await clickRowClose(page, live.trace_id)

  await expect(dialog).toContainText('still LIVE')
  await expect(dialog).toContainText(
    live.tier === 'tmux' ? '/exit' : 'stop the run first')
  await page.screenshot({ path: 'test-results/live-close-dialog.png' })

  await dialog.getByRole('button', { name: /cancel/i }).click()
  await expect(dialog).toBeHidden()
})

test('an unreachable session falls back, and cancel stops everything', async ({ page }) => {
  // Every facet wide open, so the trace-id filter below is the only narrowing.
  await page.addInitScript(() => {
    localStorage.setItem('regin_sessions_range', 'all')
    localStorage.setItem('regin_sessions_kind', 'all')
    localStorage.setItem('regin_sessions_status', '[]')
    localStorage.setItem('regin_sessions_tag', '')
    localStorage.setItem('regin_sessions_repo', 'all')
  })
  const shutdowns = []
  page.on('request', r => {
    if (r.url().includes('/shutdown')) shutdowns.push(r.url())
  })

  await page.goto('/trace/sessions')
  await page.getByRole('button', { name: /Filters/ }).click()
  await page.locator('#filters-trace-id').fill(ownTraceId)
  await page.locator('#filters-trace-id').press('Enter')
  await page.keyboard.press('Escape')
  await expect(page.locator('.srow')).toHaveCount(1)
  const dialog = await clickRowClose(page, ownTraceId)

  await expect(dialog).toBeVisible()
  await expect(dialog).not.toContainText('still LIVE')

  await dialog.getByRole('button', { name: /cancel/i }).click()
  await expect(dialog).toBeHidden()
  // Cancel means cancel: nothing was asked to shut down.
  expect(shutdowns).toHaveLength(0)
})
