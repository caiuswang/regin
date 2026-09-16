/**
 * The Agent Bridge settings tab.
 *
 * A config value with no UI is invisible and effectively unconfigurable for
 * anyone who does not hand-edit JSON — and `SECTIONS`, `BLOCK_META` and the
 * sidebar buttons in SettingsView.vue are three hand-maintained lists, so a
 * new block reaches the API and nowhere else unless all three are edited.
 * This spec is the gate on that: it renders the real tab and drives the real
 * controls.
 */
import { test, expect } from './auth-fixture.js'

// Serial: the last test toggles a real setting on the shared scratch server.
test.describe.configure({ mode: 'serial' })

const terminalSwitch = (page) => page.getByRole('switch', { name: 'terminal_enabled' })

test('the pane-terminal switch is reachable, defaults off, and says what it grants', async ({ page }) => {
  await page.goto('/settings?section=agent-bridge')
  await expect(page.getByRole('heading', { name: 'Agent Bridge' })).toBeVisible()
  await expect(terminalSwitch(page)).toBeVisible()

  const row = page.locator('tr', { hasText: 'Pane terminal enabled (full shell access)' })
  // The DEFAULT, not the live value: this suite's own scratch server forces
  // the flag on so `terminal-pane.spec.js` has something to attach to.
  await expect(row).toContainText('default false')
  await expect(row).toContainText('UNFILTERED shell access')
  await expect(row).toContainText('Admin role is required')
})

test('the sidebar offers the tab, and the bridge token is not in the payload', async ({ page }) => {
  await page.goto('/settings')
  await page.getByRole('button', { name: 'Agent Bridge', exact: true }).click()
  await expect(page.getByText('Message steering enabled')).toBeVisible()

  // The design doc's standing invariant: the bridge bearer token is the
  // credential for headless callers and never reaches the browser.
  const payload = await page.evaluate(async () => {
    const res = await fetch('/api/settings/agent-bridge', {
      headers: { Authorization: `Bearer ${localStorage.getItem('regin_auth_token')}` },
    })
    return res.text()
  })
  expect(payload).not.toContain('"token"')
  expect(JSON.parse(payload).fields.map(f => f.key)).toContain('terminal_enabled')
})

test('a save round-trips, and does not drop the token it never showed', async ({ page, request }) => {
  // `max_text_len`, not the terminal switch: `terminal-pane.spec.js` needs
  // that switch on, and these files run in parallel workers against one
  // scratch server. This still exercises the same block PUT.
  await page.goto('/settings?section=agent-bridge')
  const field = page.getByLabel('max_text_len')
  const original = await field.inputValue()
  const changed = String(Number(original) + 500)

  await field.fill(changed)
  await page.getByRole('button', { name: 'Save settings' }).click()
  await expect(page.getByText(/saved/i).first()).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('max_text_len')).toHaveValue(changed)

  // The invariant a partial PUT has broken before: a field the UI never
  // showed must survive a save made from that UI.
  const token = await page.evaluate(async () => {
    const res = await fetch('/api/settings/agent-bridge', {
      headers: { Authorization: `Bearer ${localStorage.getItem('regin_auth_token')}` },
    })
    return (await res.text()).includes('"token"')
  })
  expect(token).toBe(false)

  await page.getByLabel('max_text_len').fill(original)
  await page.getByRole('button', { name: 'Save settings' }).click()
  await expect(page.getByText(/saved/i).first()).toBeVisible()
})
