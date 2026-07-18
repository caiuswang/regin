/**
 * Regression: on mobile, the session-trace header must not change height/width
 * when a live poll flips into its "reloading" state. Two causes were fixed in
 * SessionTraceHeader.vue:
 *   1. the reload button label swapped "Reload"→"Reloading…", widening the
 *      button; as a flex sibling of the metrics column that squeezed the
 *      metrics into an extra wrapped line — so scrolled to the top of a live
 *      session the header grew/shrank ~15px every poll and jerked the page.
 *   2. the `peaked` context chip flipped null↔value on peak≈live jitter.
 * This asserts the reload control's width is identical idle vs reloading, which
 * is what kept the metrics column (and thus header height) from reflowing.
 */
import { test as authTest, expect } from './auth-fixture.js'
import { devices } from '@playwright/test'

const mobile = authTest.extend({})
mobile.use({ ...devices['Pixel 7'] })

const SID = '02537478-02be-46d7-b7cd-459f9c0a2b20'

mobile('reload control keeps a constant width when reloading (no header reflow)', async ({ page }) => {
  await page.goto(`/trace/sessions/${SID}`)

  const reloadBtn = page.locator('header').getByRole('button', { name: /reload/i }).first()
  await reloadBtn.waitFor({ timeout: 15000 })
  await page.waitForTimeout(500)

  const width = async () => (await reloadBtn.boundingBox())?.width ?? -1
  const idleW = await width()

  // Hold the reload in flight so the button sits in its "reloading" state.
  let release
  const gate = new Promise((r) => { release = r })
  await page.route('**/api/sessions/**', async (route) => { await gate; return route.continue() })
  await reloadBtn.click().catch(() => {})
  await page.waitForTimeout(250)
  const reloadingW = await width()
  release()

  // Constant-width label → the metrics column never reflows, header stays put.
  expect(Math.abs(reloadingW - idleW), `reload button width changed ${idleW}->${reloadingW}`).toBeLessThanOrEqual(1)
})
