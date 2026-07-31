import { test, expect } from './auth-fixture.js'

// The Agent SDK settings tab. The block renderer is generic, so what is worth
// asserting is the wiring the generic path can't guarantee: the deep link, the
// picker offering the launch surface's own vocabulary, the list field's
// tool-shaped affordances, and the shadowed-gating banner.

async function openTab(page, errors) {
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()) })
  page.on('pageerror', e => errors.push(String(e)))
  await page.goto('/settings?section=agent-sdk')
  await page.waitForSelector('text=SDK-launched sessions enabled', { timeout: 15000 })
}

async function pageOverflow(page) {
  return page.evaluate(() => {
    const el = document.querySelector('.content-scroll') || document.documentElement
    return { scrollWidth: el.scrollWidth, clientWidth: el.clientWidth }
  })
}

test('deep-links to the tab and renders every group', async ({ page }) => {
  const errors = []
  await page.setViewportSize({ width: 1440, height: 900 })
  await openTab(page, errors)

  for (const group of ['General', 'Lifecycle', 'Gating']) {
    await expect(page.locator('.sv-group-label', { hasText: group })).toBeVisible()
  }

  const overflow = await pageOverflow(page)
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth)
  expect(errors).toEqual([])
})

test('permission mode offers the CLI vocabulary', async ({ page }) => {
  const errors = []
  await openTab(page, errors)
  await page.locator('[aria-label="permission_mode"]').click()
  expect(await page.locator('[role="option"]').allTextContents()).toEqual(
    ['default', 'acceptEdits', 'plan', 'bypassPermissions', 'dontAsk', 'auto'])
  expect(errors).toEqual([])
})

test('gated tools edits as a tool list, not a command list', async ({ page }) => {
  const errors = []
  await openTab(page, errors)
  // The list starts empty, so the entry input exists only after adding one.
  await page.locator('text=+ Add tool').first().click()
  await expect(page.locator('[aria-label="gated_tools entry"]').first())
    .toHaveAttribute('placeholder', 'Bash')
  expect(errors).toEqual([])
})

test('renders in dark theme', async ({ page }) => {
  const errors = []
  await page.addInitScript(() => localStorage.setItem('regin_theme', 'dark'))
  await openTab(page, errors)
  expect(await page.evaluate(
    () => document.documentElement.getAttribute('data-theme'))).toBe('dark')
  expect(errors).toEqual([])
})

test('mobile 390px does not scroll the page sideways', async ({ page }) => {
  const errors = []
  await page.setViewportSize({ width: 390, height: 844 })
  await openTab(page, errors)
  const overflow = await pageOverflow(page)
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth)
  expect(errors).toEqual([])
})

test('a field-level validation error names the field, not "Failed to save"', async ({ page }) => {
  const errors = []
  await openTab(page, errors)
  // Intercepted so the assertion is about the error path, not about writing a
  // bad value into the developer's real settings file.
  await page.route('**/api/settings/agent-sdk', async route => {
    if (route.request().method() !== 'PUT') return route.continue()
    return route.fulfill({
      status: 400,
      contentType: 'application/json',
      body: JSON.stringify({ ok: false, errors: ['max_concurrent_runs must be ≥ 1'] }),
    })
  })
  await page.locator('text=Save settings').click()
  await expect(page.locator('text=max_concurrent_runs must be ≥ 1')).toBeVisible()
  // No console-error assertion here: the deliberate 400 logs one by itself.
})

test('shadowed-gating warning from the GET payload reaches the page', async ({ page }) => {
  const errors = []
  // Injected rather than configured: asserting the render path must not depend
  // on mutating the developer's real settings file.
  await page.route('**/api/settings/agent-sdk', async route => {
    if (route.request().method() !== 'GET') return route.continue()
    const res = await route.fetch()
    const body = await res.json()
    body.warnings = ['Gating is configured but inert: test reason.']
    return route.fulfill({ response: res, body: JSON.stringify(body) })
  })
  await openTab(page, errors)
  await expect(page.locator('text=Gating is configured but inert')).toBeVisible()
  expect(errors).toEqual([])
})
