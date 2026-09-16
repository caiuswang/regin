/**
 * Following the tail folds the header. `enableFollow` reaches the bottom with a
 * PROGRAMMATIC scroll, which the collapse machine deliberately ignores (see
 * canAutoCollapse's input gate) — so on a freshly-opened trace the pill used to
 * stick the reader to the newest span with ~200px of header still pinned above
 * it. The fold is therefore asked for explicitly, and these tests pin the three
 * things that must NOT come with it: no pin (the reveal pull still works), no
 * re-expand when following stops, and nothing at all below lg.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'
import { traceScrollTop, wheelOverTrace } from './helpers/traceScroller.js'

async function seedScrollableSession(page) {
  const traceId = randomUUID()
  const spans = []
  const filler = 'Lorem ipsum dolor sit amet, consectetur adipiscing elit. '.repeat(30)
  for (let i = 0; i < 20; i++) {
    const pid = `p-${traceId.slice(0, 8)}-${i}`
    spans.push({
      trace_id: traceId, span_id: pid, parent_id: null, name: 'prompt',
      start_time: `2026-05-09T10:${String(i).padStart(2, '0')}:00`,
      attributes: { text: `prompt number ${i} — padding the feed so the page scrolls`, is_test: true },
    })
    spans.push({
      trace_id: traceId, span_id: `a-${traceId.slice(0, 8)}-${i}`, parent_id: pid,
      name: 'assistant_response',
      start_time: `2026-05-09T10:${String(i).padStart(2, '0')}:05`,
      attributes: { text: filler, is_test: true },
    })
  }
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  return traceId
}

const pill = (page) => page.locator('button.follow-pill')

test('Follow latest folds the header without pinning it shut', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const traceId = await seedScrollableSession(page)
  await page.goto(`/trace/sessions/${traceId}`)

  const vitals = page.getByTestId('trace-vitals-strip')
  const digest = page.getByTestId('trace-header-digest')

  // Fresh navigation, scroll at the top: header fully expanded.
  await expect(vitals).toBeVisible({ timeout: 10_000 })
  await expect(digest).toBeHidden()
  await expect(pill(page)).toContainText('Follow latest')

  await pill(page).click()
  await expect(pill(page)).toContainText('Following')
  await expect(digest).toBeVisible()
  await expect(vitals).toBeHidden()

  // Not pinned: the sustained reveal pull must still bring the header back.
  // The pull only counts once the reader is resting at the top, and following
  // parked us at the bottom — so travel there first (which is also what drops
  // follow-tail), then pull.
  await wheelOverTrace(page, -3000, 12)
  await expect.poll(() => traceScrollTop(page),
    { message: 'scroller actually reached the top' }).toBeLessThan(24)
  await expect(digest).toBeVisible()
  await wheelOverTrace(page, -60, 24)
  await expect(vitals).toBeVisible()
  await expect(digest).toBeHidden()
})

test('leaving follow-tail does not push the header back open', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const traceId = await seedScrollableSession(page)
  await page.goto(`/trace/sessions/${traceId}`)

  const vitals = page.getByTestId('trace-vitals-strip')
  const digest = page.getByTestId('trace-header-digest')
  await expect(vitals).toBeVisible({ timeout: 10_000 })

  await pill(page).click()
  await expect(digest).toBeVisible()

  // Stopping the auto-scroll is not a request for the header back; re-expanding
  // here would shove the transcript down under a reader who only wanted it to
  // hold still.
  await pill(page).click()
  await expect(pill(page)).toContainText('Follow latest')
  await page.waitForTimeout(400)
  await expect(digest).toBeVisible()
  await expect(vitals).toBeHidden()
})

test('below lg the follow pill leaves the header alone', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 900 })
  const traceId = await seedScrollableSession(page)
  await page.goto(`/trace/sessions/${traceId}`)

  const vitals = page.getByTestId('trace-vitals-strip')
  await expect(vitals).toBeVisible({ timeout: 10_000 })

  // The header isn't sticky here, so folding it would only unmount the strips
  // out from under the reader — and the isLgUp watch would undo it anyway.
  await pill(page).click()
  await expect(pill(page)).toContainText('Following')
  await page.waitForTimeout(600)
  await expect(vitals).toBeVisible()
})

test('the follow-fold survives a prior H press — the pin does not strand it', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const traceId = await seedScrollableSession(page)
  await page.goto(`/trace/sessions/${traceId}`)

  const vitals = page.getByTestId('trace-vitals-strip')
  const digest = page.getByTestId('trace-header-digest')
  await expect(vitals).toBeVisible({ timeout: 10_000 })

  // H twice leaves the header expanded but PINNED — the state that used to make
  // the follow-fold unrecoverable, since the reveal pull refuses to fire while
  // pinned and only the pull ever clears the pin.
  await page.keyboard.press('h')
  await expect(digest).toBeVisible()
  await page.keyboard.press('h')
  await expect(vitals).toBeVisible()

  await pill(page).click()
  await expect(digest).toBeVisible()
  await expect(vitals).toBeHidden()

  await wheelOverTrace(page, -3000, 12)
  await expect.poll(() => traceScrollTop(page),
    { message: 'scroller actually reached the top' }).toBeLessThan(24)
  await wheelOverTrace(page, -60, 24)
  await expect(vitals).toBeVisible()
})

test('a follow-fold is undone by a resize below lg, like any unpinned fold', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const traceId = await seedScrollableSession(page)
  await page.goto(`/trace/sessions/${traceId}`)

  const vitals = page.getByTestId('trace-vitals-strip')
  const digest = page.getByTestId('trace-header-digest')
  await expect(vitals).toBeVisible({ timeout: 10_000 })

  await page.keyboard.press('h')   // arm the pin
  await page.keyboard.press('h')
  await pill(page).click()
  await expect(digest).toBeVisible()

  // Below lg the header isn't sticky, so a fold that survived the resize would
  // carry its own Details button off-screen.
  await page.setViewportSize({ width: 900, height: 900 })
  await expect(vitals).toBeVisible()
})
