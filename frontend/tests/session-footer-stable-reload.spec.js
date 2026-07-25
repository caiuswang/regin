/**
 * Regression: the trace feed footer ("End of timeline" ↔ spinner "Loading")
 * must keep a constant height across the reload swap. It sits at the bottom of
 * the feed; parked at the bottom of a live session, a per-poll height change
 * clamped the scroll and the feed twitched up/down every few seconds. The
 * fix pins a fixed-height, same-font-size row so the swap can't change height.
 */
import { test as authTest, expect } from './auth-fixture.js'
import { devices } from '@playwright/test'

const SID = '02537478-02be-46d7-b7cd-459f9c0a2b20'
const mobile = authTest.extend({})
mobile.use({ ...devices['Pixel 7'] })

mobile('feed footer keeps a constant height when reloading', async ({ page }) => {
  await page.goto(`/trace/sessions/${SID}`)
  await page.locator('header').first().waitFor({ timeout: 15000 })
  await page.waitForTimeout(1500)

  // Located structurally, not by its text: in conversation mode the idle
  // label is blank (the feed column prints its own end-of-timeline marker),
  // so the fixed-height row is the only stable handle — and the height
  // invariant is exactly what this test is about.
  const footerH = () => page.evaluate(() => {
    const f = document.querySelector('.pb-20')
    return f ? Math.round(f.getBoundingClientRect().height) : -1
  })

  // The old locator found the footer BY its text, so it doubled as proof the
  // end-of-feed marker renders at all. That assertion would have been lost in
  // the retarget, so state it separately: in conversation mode the marker is
  // printed by the feed column rather than this row.
  await expect(page.getByText('End of timeline').first()).toBeVisible({ timeout: 15000 })

  const idleH = await footerH()
  expect(idleH, 'footer not found').toBeGreaterThan(0)

  // Hold a reload in flight so the footer shows its "Loading" state.
  let release
  const gate = new Promise((r) => { release = r })
  await page.route('**/api/sessions/**', async (route) => { await gate; return route.continue() })
  await page.locator('header').getByRole('button', { name: /reload/i }).first().click().catch(() => {})
  await page.waitForTimeout(300)
  const reloadingH = await footerH()
  release()

  expect(Math.abs(reloadingH - idleH), `footer height changed ${idleH}->${reloadingH}`).toBeLessThanOrEqual(1)
})
