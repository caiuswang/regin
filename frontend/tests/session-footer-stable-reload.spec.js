/**
 * Regression: the trace feed footer must stay quiet across a reload.
 *
 * It used to swap "End of timeline" for a spinner + "Loading" on every reload —
 * including each live-poll tick — which read as the page blinking (CAI-16).
 * Reload progress now lives only on the header's Reload button. The footer's
 * fixed-height row stays: parked at the bottom of a live session, a per-poll
 * height change clamped the scroll and the feed twitched up/down.
 *
 * First-load feedback is separate (the "Loading session…" empty state) and is
 * asserted below so removing the footer indicator can't silently take it too.
 */
import { test as authTest, expect } from './auth-fixture.js'
import { devices } from '@playwright/test'
import { BASELINE_TRACE } from './helpers/fixtures.js'

// The seeded baseline session (`scripts/e2e-seed.py`). This was a hardcoded
// UUID from one developer's real database, so the spec only ever ran there.
const SID = BASELINE_TRACE
const mobile = authTest.extend({})
mobile.use({ ...devices['Pixel 7'] })

mobile('feed footer stays quiet and fixed-height when reloading', async ({ page }) => {
  await page.goto(`/trace/sessions/${SID}`)
  await page.locator('header').first().waitFor({ timeout: 15000 })
  await page.waitForTimeout(1500)

  // Located structurally, not by its text: in conversation mode the idle
  // label is blank (the feed column prints its own end-of-timeline marker),
  // so the fixed-height row is the only stable handle — and the height
  // invariant is exactly what this test is about.
  const footer = page.locator('.pb-20').first()
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

  // Hold a reload in flight, then look at the footer while it is pending.
  let release
  const gate = new Promise((r) => { release = r })
  await page.route('**/api/sessions/**', async (route) => { await gate; return route.continue() })
  const reloadBtn = page.locator('header').getByRole('button', { name: /reload/i }).first()
  await reloadBtn.click().catch(() => {})
  await page.waitForTimeout(300)

  const reloadingH = await footerH()
  expect(await footer.innerText()).not.toMatch(/loading/i)
  expect(await footer.locator('svg').count(), 'footer spinner during reload').toBe(0)
  // The busy state the user is supposed to read stays where it was.
  await expect(reloadBtn).toBeDisabled()
  await expect(reloadBtn.locator('.animate-spin')).toBeVisible()

  release()
  expect(Math.abs(reloadingH - idleH), `footer height changed ${idleH}->${reloadingH}`).toBeLessThanOrEqual(1)
})

mobile('first load still announces itself while the session request is pending', async ({ page }) => {
  let release
  const gate = new Promise((r) => { release = r })
  await page.route(`**/api/sessions/${SID}/map*`, async (route) => { await gate; return route.continue() })

  await page.goto(`/trace/sessions/${SID}`)
  await expect(page.getByText('Loading session')).toBeVisible({ timeout: 15000 })

  release()
  await expect(page.getByText('End of timeline').first()).toBeVisible({ timeout: 15000 })
})
