import { test, expect } from './auth-fixture.js'

// The hook lifecycle diagram is per-agent: every agent has a different hook
// system, so the diagram lists ONLY the selected agent's events — events it
// never fires are absent, not pretended-into its flow. This pins that the
// diagram reads each provider's `supported_events` off the handlers API.

const hookStatus = {
  providers: [
    { id: 'claude', name: 'Claude Code', active: true, hooks_supported: true,
      hook_settings_path: '/tmp/claude-settings.json',
      hook_manager: { installed: true, target: 'claude', routed_events: ['PreToolUse'] },
      debug: { installed: false, target: 'claude' } },
    { id: 'kimi', name: 'Kimi Code', active: false, hooks_supported: true,
      hook_settings_path: '/tmp/kimi-config.toml',
      hook_manager: { installed: true, target: 'kimi', routed_events: ['PreToolUse'] },
      debug: { installed: false, target: 'kimi' } },
  ],
  hook_manager: { installed: true, target: 'claude' },
  debug: { installed: false, target: 'claude' },
}

// One shared handler so both providers render the same node grid; only the
// supported_events differ.
const handler = {
  name: 'pre_tool_trace', label: 'Pre tool trace', summary: '', match_hint: '',
  events: ['PreToolUse'], wired_events: ['PreToolUse'], wired: true,
  kind: 'trace', priority: 10, enabled: true,
}

const CLAUDE_EVENTS = [
  'SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PermissionRequest',
  'PostToolUse', 'PostToolUseFailure', 'SubagentStart', 'SubagentStop',
  'TaskCreated', 'TaskCompleted', 'Stop', 'StopFailure', 'TeammateIdle',
  'PreCompact', 'PostCompact', 'SessionEnd', 'Notification',
]
// Kimi's lifecycle: no PermissionRequest / TaskCreated / TeammateIdle.
const KIMI_EVENTS = [
  'SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse',
  'PostToolUseFailure', 'SubagentStart', 'SubagentStop', 'Stop',
  'StopFailure', 'PreCompact', 'PostCompact', 'SessionEnd', 'Notification',
]

const handlers = {
  claude: { installed: true, provider: 'claude', routed_events: ['PreToolUse'],
            config_path: '/tmp/claude-hm.json', supported_events: CLAUDE_EVENTS,
            handlers: [handler] },
  kimi: { installed: true, provider: 'kimi', routed_events: ['PreToolUse'],
          config_path: '/tmp/kimi-hm.json', supported_events: KIMI_EVENTS,
          handlers: [handler] },
}

test.describe('Per-agent hook lifecycle diagram', () => {
  test('lists only the selected agent’s events', async ({ page }) => {
    await page.route('**/api/settings', r => r.fulfill({ json: [] }))
    await page.route('**/api/hooks', r => r.fulfill({ json: hookStatus }))
    await page.route('**/api/hooks/handlers?provider=*', async route => {
      const url = new URL(route.request().url())
      await route.fulfill({ json: handlers[url.searchParams.get('provider')] })
    })
    await page.route('**/api/doctor', r => r.fulfill({ json: { groups: [], project: { name: 'P', items: [] } } }))

    await page.goto('/settings')
    await page.locator('button', { hasText: 'Hook Handlers' }).click()
    await page.locator('button', { hasText: 'Show diagram' }).click()

    const diagram = page.locator('.vue-flow')
    await expect(diagram).toBeVisible()

    // Claude (default) fires every spec event → its nodes are all present.
    await expect(diagram.getByText('PermissionRequest', { exact: true })).toBeVisible()
    await expect(diagram.getByText('TaskCreated / TaskCompleted', { exact: true })).toBeVisible()

    // Switch to Kimi → only Kimi's events remain; the ones it never fires
    // (PermissionRequest, TaskCreated, TeammateIdle, …) are gone entirely.
    await page.getByRole('button', { name: 'Kimi Code' }).click()
    await expect(diagram.getByText('PreToolUse', { exact: true })).toBeVisible()
    await expect(diagram.getByText('PermissionRequest', { exact: true })).toHaveCount(0)
    await expect(diagram.getByText('TaskCreated / TaskCompleted', { exact: true })).toHaveCount(0)
    await expect(diagram.getByText('TeammateIdle', { exact: true })).toHaveCount(0)
  })
})
