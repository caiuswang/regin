import { test, expect } from './auth-fixture.js'
import { inboxFixture, mockInbox } from './helpers/inbox-fixture.js'

// Screenshot capture for the /inbox redesign. Every route is mocked: driving
// the real feed here marked real `send_to_user` messages read (clicking a row
// is a write), which silently emptied the operator's unread badge. A capture
// run must never mutate the inbox it is photographing.
const OUT = process.env.SHOT_DIR || '/tmp/inbox-shots'
const TAG = process.env.SHOT_TAG || 'after'

async function open(page, theme, opts) {
  // Only seed localStorage — the app's own boot script applies `data-theme`,
  // so setting it here would race that script and touch a document that does
  // not exist yet at init-script time.
  await page.addInitScript((t) => localStorage.setItem('regin_theme', t), theme)
  const errors = []
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()) })
  page.on('pageerror', e => errors.push(String(e)))
  await mockInbox(page, opts)
  await page.goto('/inbox')
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
  await page.waitForSelector('[data-testid="inbox-row"]', { timeout: 20000 })
  await page.waitForTimeout(400)
  return errors
}

test('desktop — light and dark', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  for (const theme of ['light', 'dark']) {
    const errors = await open(page, theme, { messages: inboxFixture() })
    await page.screenshot({ path: `${OUT}/${TAG}-desktop-${theme}.png` })
    expect(errors, `console errors (${theme})`).toEqual([])
  }
})

test('mobile — list and detail', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  for (const theme of ['light', 'dark']) {
    const errors = await open(page, theme, { messages: inboxFixture() })
    await page.screenshot({ path: `${OUT}/${TAG}-mobile-${theme}-list.png` })
    await page.locator('[data-testid="inbox-row"]').nth(2).click()
    await page.waitForTimeout(400)
    await page.screenshot({ path: `${OUT}/${TAG}-mobile-${theme}-detail.png` })
    expect(errors, `console errors (mobile ${theme})`).toEqual([])
  }
})

test('one detail pane per message type', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await open(page, 'light', { messages: inboxFixture() })
  const chips = page.locator('[data-testid="inbox-kind-chip"]')
  const captured = []
  for (let i = 0; i < await chips.count(); i++) {
    const chip = chips.nth(i)
    if (await chip.isDisabled()) continue
    const label = (await chip.textContent()).trim().split(/\s+/)[0].toLowerCase()
    await chip.click()
    await page.waitForTimeout(250)
    if (await page.locator('[data-testid="inbox-row"]').count()) {
      await page.locator('[data-testid="inbox-row"]').first().click()
      await page.waitForTimeout(250)
      await page.locator('.inbox-detail-pane')
        .screenshot({ path: `${OUT}/${TAG}-type-${label}.png` })
      captured.push(label)
    }
    await chip.click()
    await page.waitForTimeout(150)
  }
  console.log('TYPES_CAPTURED', JSON.stringify(captured))
  expect(captured.length, 'should cover every message type').toBeGreaterThan(6)
})

test('decision state — desktop and mobile', async ({ page }) => {
  for (const theme of ['light', 'dark']) {
    await page.setViewportSize({ width: 1440, height: 900 })
    await open(page, theme, { messages: inboxFixture({ withDecisions: true }) })
    await page.waitForSelector('.inbox-decision')
    await page.screenshot({ path: `${OUT}/${TAG}-decision-desktop-${theme}.png` })
  }

  await page.setViewportSize({ width: 390, height: 844 })
  await open(page, 'light', { messages: inboxFixture({ withDecisions: true }) })
  await page.screenshot({ path: `${OUT}/${TAG}-decision-mobile-list.png` })
  await page.locator('[data-testid="inbox-row"]').first().click()
  await page.waitForSelector('.inbox-decision')
  await page.screenshot({ path: `${OUT}/${TAG}-decision-mobile-detail.png` })
})
