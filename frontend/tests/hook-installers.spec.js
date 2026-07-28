/**
 * Settings › Hook Installers — the surface added for CAI-21.
 *
 * Asserts the panel renders per-provider install state and, crucially, that a
 * hook whose command has drifted from what install writes today offers a
 * Refresh action. Before this panel existed, stale wiring (CAI-15) could only
 * be repaired by curl-ing the API or hand-editing the provider config.
 */
import { test, expect } from './auth-fixture.js'

const ROUTE = '/settings?section=install'

test('hook installers list every provider with its settings path', async ({ page }) => {
  await page.goto(ROUTE)
  const panel = page.locator('.sv-section-title', { hasText: 'Hook Installers' })
  await expect(panel).toBeVisible()
  await expect(page.getByText('Hook Manager').first()).toBeVisible()
  await expect(page.getByText('Debug Hook').first()).toBeVisible()
})

test('an installed hook exposes Refresh and its exact commands', async ({ page }) => {
  await page.goto(ROUTE)
  // `.card` nests (the provider Card wraps each HookCard), so the innermost —
  // last in document order — is the hook card itself.
  const installed = page.locator('.card', { hasText: 'Installs the unified hook dispatcher' })
    .filter({ has: page.getByText('Installed', { exact: true }) }).last()
  await expect(installed).toBeVisible()

  await expect(installed.getByRole('button', { name: 'Refresh' })).toBeVisible()
  await expect(installed.getByRole('button', { name: 'Remove' })).toBeVisible()

  await installed.getByRole('button', { name: /Show commands/ }).click()
  await expect(installed.locator('pre').first()).toContainText('-m hook_manager')
})

test('stale wiring reads Needs repair and explains the fix', async ({ page }) => {
  // Serve a drifted command so the card takes its stale branch without
  // touching the real provider config on this machine.
  await page.route('**/api/hooks', async route => {
    await route.fulfill({
      json: {
        providers: [{
          id: 'claude',
          name: 'Claude Code',
          active: true,
          hooks_supported: true,
          hook_settings_path: '/tmp/claude-settings.json',
          hook_manager: {
            installed: true,
            stale: true,
            target: 'claude',
            routed_events: ['PostToolUse'],
            commands: { PostToolUse: ['/old/python -m hook_manager PostToolUse'] },
            expected_commands: { PostToolUse: '/new/python -P -m hook_manager PostToolUse --agent-type claude' },
            stale_events: ['PostToolUse'],
            missing_events: [],
          },
          debug: {
            installed: false, stale: false, target: 'claude', routed_events: [],
            commands: {}, expected_commands: {}, stale_events: [], missing_events: [],
          },
        }],
        hook_manager: { installed: true, stale: true, target: 'claude' },
        debug: { installed: false, stale: false, target: 'claude' },
      },
    })
  })

  await page.goto(ROUTE)
  const card = page.locator('.card', { hasText: 'Installs the unified hook dispatcher' }).last()
  await expect(card.getByText('Needs repair')).toBeVisible()
  await expect(card.getByText(/not the one regin writes today/)).toBeVisible()

  await card.getByRole('button', { name: /Show commands/ }).click()
  await expect(card.getByText('stale')).toBeVisible()
  await expect(card.locator('pre').last()).toContainText('--agent-type claude')
})

test('another checkout offers Adopt instead of Install (CAI-26)', async ({ page }) => {
  // A moved checkout reads as not-installed, so the card used to offer only
  // Install — which adds a second entry beside the old one, both then firing.
  await page.route('**/api/hooks', async route => {
    await route.fulfill({
      json: {
        providers: [{
          id: 'claude',
          name: 'Claude Code',
          active: true,
          hooks_supported: true,
          hook_settings_path: '/tmp/claude-settings.json',
          hook_manager: {
            installed: false,
            stale: false,
            target: 'claude',
            routed_events: [],
            commands: {},
            expected_commands: {},
            stale_events: [],
            missing_events: [],
            foreign_events: ['PostToolUse'],
            foreign_roots: ['/old/regin'],
          },
          debug: {
            installed: false, stale: false, target: 'claude', routed_events: [],
            commands: {}, expected_commands: {}, stale_events: [], missing_events: [],
            foreign_events: [], foreign_roots: [],
          },
        }],
        hook_manager: { installed: false, stale: false, target: 'claude' },
        debug: { installed: false, stale: false, target: 'claude' },
      },
    })
  })

  await page.goto(ROUTE)
  const card = page.locator('.card', { hasText: 'Installs the unified hook dispatcher' }).last()
  await expect(card.getByText('Other checkout')).toBeVisible()
  await expect(card.getByText(/runs out of \/old\/regin/)).toBeVisible()
  await expect(card.getByRole('button', { name: 'Adopt' })).toBeVisible()
  await expect(card.getByRole('button', { name: 'Install' })).toHaveCount(0)
})
