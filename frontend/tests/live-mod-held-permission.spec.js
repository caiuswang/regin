/**
 * A permission request regin-permit holds open in `tool.check` (F158 S3):
 * `regin bridge hold` writes the call's `pending-<tool_use_id>` tool row with
 * `mod_held`, and while that hold stands `live-state.mod_held` lists the
 * call. The card then decides it like the SDK tier — Allow / Deny, POSTed
 * with the `tool_use_id` — never by a tmux position pick. Once the hold has
 * ended (released to the terminal, the hold process gone) the same card is
 * the native dialog's, so it falls back to the tmux branch.
 *
 * Browser-side contract only: `live-state` and `bridge-decide` are stubbed
 * (tests/agent_bridge/test_mod_hold.py owns the server side).
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'
import { settle } from './helpers/overflow.js'

test.use({ viewport: { width: 375, height: 667 } })

async function stubMap(page, traceId, { reachable }) {
  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    const resp = await route.fetch()
    const json = await resp.json()
    await route.fulfill({
      response: resp,
      json: {
        ...json,
        bridge_reachable: reachable,
        bridge_pane: reachable ? '%7' : null,
        sdk_owned: false,
        last_seen: new Date(Date.now() - 30_000).toISOString(),
      },
    })
  })
}

async function stubLiveState(page, traceId, heldIds) {
  await page.route(`**/api/sessions/${traceId}/live-state`, route => route.fulfill({
    json: { mod_listener: true, mod_held: heldIds.map(id => ({ tool_use_id: id })) },
  }))
}

async function stubDecide(page, traceId) {
  const posts = []
  await page.route(`**/api/sessions/${traceId}/bridge-decide`, async (route) => {
    posts.push(route.request().postDataJSON())
    await route.fulfill({ json: { id: 1, delivered: true, detail: 'decision delivered' } })
  })
  return posts
}

async function stubMenu(page, traceId) {
  const gets = []
  await page.route(`**/api/sessions/${traceId}/bridge-menu`, async (route) => {
    gets.push(true)
    await route.fulfill({ json: { parsed: false, detail: 'no menu on screen' } })
  })
  return gets
}

// The row `regin bridge hold` writes (lib/agent_bridge/mod_hold.card_span):
// the call's own PENDING tool row, marked `mod_held`.
async function seedHeldCard(page, { reachable = true } = {}) {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const toolUseId = `toolu_${sfx}`
  const spanId = `pending-${toolUseId}`
  const now = new Date().toISOString()
  const res = await page.request.post('/api/session-spans', { data: [
    { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
      start_time: now, attributes: { text: 'held-permission fixture', is_test: true } },
    { trace_id: traceId, span_id: spanId, parent_id: null, name: 'tool.Bash',
      start_time: now, status_code: 'PENDING',
      attributes: { tool_name: 'Bash', tool_use_id: toolUseId, live: true,
        command_preview: 'rm -rf build', gate_key: 'g-1', mod_held: true,
        is_test: true } },
  ] })
  expect(res.ok()).toBeTruthy()
  await stubMap(page, traceId, { reachable })
  return { traceId, spanId, toolUseId }
}

async function openSheet(page, traceId, spanId) {
  await page.goto(`/live/${traceId}`)
  await settle(page)
  const row = page.locator(`[data-testid="live-row"][data-span-id="${spanId}"]`)
  await expect(row).toBeVisible({ timeout: 10_000 })
  await row.click()
  const sheet = page.locator('[data-testid="live-sheet"]')
  await expect(sheet).toBeVisible()
  return sheet
}

test.describe('Decide a permission request held by regin-permit', () => {
  test('test_held_permission_card_offers_allow_and_deny', async ({ page }) => {
    const { traceId, spanId, toolUseId } = await seedHeldCard(page)
    await stubLiveState(page, traceId, [toolUseId])
    const menuGets = await stubMenu(page, traceId)
    const posts = await stubDecide(page, traceId)

    const sheet = await openSheet(page, traceId, spanId)
    await expect(sheet).toContainText('rm -rf build')
    await expect(sheet.locator('[data-testid="live-qa-allow"]')).toBeVisible()
    await expect(sheet.locator('[data-testid="live-qa-deny"]')).toBeVisible()
    await expect(sheet.locator('[data-testid="live-qa-decide-pick"]')).toHaveCount(0)
    expect(menuGets.length).toBe(0)

    await sheet.locator('[data-testid="live-qa-allow"]').click()
    await sheet.locator('[data-testid="live-qa-decide-send"]').click()
    await expect.poll(() => posts.length).toBe(1)
    expect(posts[0]).toEqual({ behavior: 'allow', tool_use_id: toolUseId })
    await expect(sheet).toBeHidden({ timeout: 5_000 })
  })

  test('deny sends the typed reason with the held call', async ({ page }) => {
    const { traceId, spanId, toolUseId } = await seedHeldCard(page, { reachable: false })
    await stubLiveState(page, traceId, [toolUseId])
    const posts = await stubDecide(page, traceId)

    const sheet = await openSheet(page, traceId, spanId)
    await sheet.locator('[data-testid="live-qa-deny"]').click()
    await sheet.locator('[data-testid="live-qa-decide-reason"]').fill('not on main')
    await sheet.locator('[data-testid="live-qa-decide-send"]').click()

    await expect.poll(() => posts.length).toBe(1)
    expect(posts[0]).toEqual({ behavior: 'deny', reason: 'not on main', tool_use_id: toolUseId })
  })

  test('a card whose hold has ended goes back to the tmux branch', async ({ page }) => {
    const { traceId, spanId } = await seedHeldCard(page)
    await stubLiveState(page, traceId, [])
    const menuGets = await stubMenu(page, traceId)

    const sheet = await openSheet(page, traceId, spanId)
    await expect(sheet.locator('[data-testid="live-qa-decide-unavailable"]')).toBeVisible()
    await expect(sheet.locator('[data-testid="live-qa-allow"]')).toHaveCount(0)
    expect(menuGets.length).toBe(1)
  })

  test('an ended hold with no pane says to answer in the terminal', async ({ page }) => {
    const { traceId, spanId } = await seedHeldCard(page, { reachable: false })
    await stubLiveState(page, traceId, [])
    const menuGets = await stubMenu(page, traceId)

    const sheet = await openSheet(page, traceId, spanId)
    await expect(sheet.locator('[data-testid="live-qa-decide-unavailable"]')).toBeVisible()
    await expect(sheet.locator('[data-testid="live-qa-allow"]')).toHaveCount(0)
    expect(menuGets.length).toBe(0)
  })
})
