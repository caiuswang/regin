/**
 * The trace list's Close, when regin could not tell whether the session is
 * still running.
 *
 * `/live-state` answers `undetermined: true` with a NULL tier — the registry
 * named a pane it then failed to read. That null looks exactly like the null
 * of "nothing holds this session", and treating it as one is how a row gets
 * settled with no warning and no shutdown attempt while the agent keeps going.
 * So this pins the two things that separate them: the dialog says regin could
 * not tell, and the confirm still fires a shutdown request.
 *
 * The sibling live-shutdown-dialog.spec.js covers the reachable/unreachable
 * cases against whatever this host happens to be running; these two are routed
 * so the undetermined branch is exercised on every machine.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const DIALOG = '.fixed.inset-0.z-50'

const UNDETERMINED = {
  tier: null, live: false, starting: false, undetermined: true,
  detail: 'a pane is registered but regin could not read it; the session may still be running',
}

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

// A span with a fresh start_time and no SessionEnd is what makes the row read
// as active — only an active row is probed, and only a non-ended row renders
// Close at all.
async function seedActiveSession(page) {
  const traceId = randomUUID()
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  // Naive LOCAL ISO, like every real span: `last_seen` is compared against the
  // server's local clock, so a UTC stamp reads as hours stale and the row is
  // never probed at all.
  const now = new Date(Date.now() - new Date().getTimezoneOffset() * 60000)
    .toISOString().replace('Z', '')
  const ok = (await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` },
    data: [{
      trace_id: traceId, span_id: `p-${traceId.slice(0, 8)}`, parent_id: null,
      name: 'prompt', start_time: now,
      attributes: { text: 'still going' },
    }],
  })).ok()
  expect(ok).toBeTruthy()
  return traceId
}

async function openClose(page, traceId) {
  await page.goto('/trace/sessions')
  await page.waitForSelector('.srow', { timeout: 15000 })
  const row = page.locator('.srow')
    .filter({ has: page.locator(`[aria-label="Copy session id ${traceId}"]`) })
  await expect(row).toHaveCount(1)
  const button = row.getByRole('button', { name: 'Close', exact: true })
  await button.scrollIntoViewIfNeeded()
  await button.click({ force: true })
  return page.locator(DIALOG).first()
}

test('an undetermined session says regin could not tell, not that it is unreachable', async ({ page }) => {
  test.setTimeout(90_000)
  const traceId = await seedActiveSession(page)
  await page.route(`**/api/sessions/${traceId}/live-state`, route =>
    route.fulfill({ json: UNDETERMINED }))

  const dialog = await openClose(page, traceId)

  await expect(dialog).toContainText('could not tell whether this session is still running')
  // Not the "no way to stop it" fallback: regin does not know that.
  await expect(dialog).not.toContainText('has no way to stop it')
  await expect(dialog).not.toContainText('still LIVE')

  await dialog.getByRole('button', { name: /cancel/i }).click()
  await expect(dialog).toBeHidden()
})

test('confirming an undetermined session still attempts the shutdown', async ({ page }) => {
  test.setTimeout(90_000)
  const traceId = await seedActiveSession(page)
  await page.route(`**/api/sessions/${traceId}/live-state`, route =>
    route.fulfill({ json: UNDETERMINED }))

  const shutdowns = []
  await page.route(`**/api/sessions/${traceId}/shutdown`, async (route) => {
    shutdowns.push(route.request().url())
    await route.fulfill({ json: { ...UNDETERMINED, closed: false, interrupted: false } })
  })

  const dialog = await openClose(page, traceId)
  await dialog.getByRole('button', { name: 'Close session', exact: true }).click()

  // Dropping the state at the probe is what skipped this request entirely.
  await expect.poll(() => shutdowns.length, { timeout: 10_000 }).toBe(1)
  // …and the row settles regardless, because a session regin could not reach
  // is exactly the one a manual close exists for. Asserted on the row rather
  // than on the flash, which self-dismisses.
  const row = page.locator('.srow')
    .filter({ has: page.locator(`[aria-label="Copy session id ${traceId}"]`) })
  await expect(row.getByRole('button', { name: 'Close', exact: true }))
    .toHaveCount(0, { timeout: 10_000 })
})
