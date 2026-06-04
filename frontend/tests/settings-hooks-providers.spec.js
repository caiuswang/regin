import { test, expect } from './auth-fixture.js'

const emptySettings = []
const hookStatus = {
  providers: [
    {
      id: 'claude',
      name: 'Claude Code',
      active: true,
      hooks_supported: true,
      hook_settings_path: '/tmp/claude-settings.json',
      hook_manager: {
        installed: true,
        target: 'claude',
        routed_events: ['SessionStart'],
      },
      debug: {
        installed: false,
        target: 'claude',
      },
    },
    {
      id: 'codex',
      name: 'OpenAI Codex',
      active: false,
      hooks_supported: true,
      hook_settings_path: '/tmp/codex-hooks.json',
      hook_manager: {
        installed: false,
        target: 'codex',
        routed_events: [],
      },
      debug: {
        installed: false,
        target: 'codex',
      },
    },
  ],
  hook_manager: { installed: true, target: 'claude' },
  debug: { installed: false, target: 'claude' },
}

const handlers = {
  claude: {
    installed: true,
    provider: 'claude',
    routed_events: ['SessionStart'],
    handlers: [
      {
        name: 'session_start',
        label: 'Session start',
        summary: 'Writes the session-start lifecycle span.',
        match_hint: '',
        events: ['SessionStart'],
        wired_events: ['SessionStart'],
        wired: true,
        kind: 'trace',
        priority: 10,
        enabled: true,
      },
    ],
  },
  codex: {
    installed: false,
    provider: 'codex',
    routed_events: [],
    handlers: [
      {
        name: 'session_start',
        label: 'Session start',
        summary: 'Writes the session-start lifecycle span.',
        match_hint: '',
        events: ['SessionStart'],
        wired_events: [],
        wired: false,
        kind: 'trace',
        priority: 10,
        enabled: true,
      },
    ],
  },
}

test.describe('Settings hook providers', () => {
  test('renders independent Claude and Codex hook installer state', async ({ page }) => {
    const calls = []

    await page.route('**/api/settings', async route => {
      await route.fulfill({ json: emptySettings })
    })
    await page.route('**/api/hooks', async route => {
      await route.fulfill({ json: hookStatus })
    })
    await page.route('**/api/hooks/handlers?provider=*', async route => {
      const url = new URL(route.request().url())
      await route.fulfill({ json: handlers[url.searchParams.get('provider')] })
    })
    await page.route('**/api/doctor', async route => {
      await route.fulfill({ json: { groups: [], project: { name: 'Project', items: [] } } })
    })
    await page.route('**/api/hooks/*/install?provider=*', async route => {
      calls.push(route.request().url())
      await route.fulfill({ json: { ok: true, msg: 'installed' } })
    })

    await page.goto('/settings')

    // Provider installer cards live behind the "Hook Installers" section.
    await page.locator('button', { hasText: 'Hook Installers' }).click()

    await expect(page.locator('h3', { hasText: 'Claude Code' })).toBeVisible()
    await expect(page.locator('code', { hasText: '/tmp/claude-settings.json' })).toBeVisible()
    await expect(page.locator('h3', { hasText: 'OpenAI Codex' })).toBeVisible()
    await expect(page.locator('code', { hasText: '/tmp/codex-hooks.json' })).toBeVisible()

    const codexCard = page.locator('.card', { hasText: 'OpenAI Codex' })
    await codexCard.getByRole('button', { name: /install/i }).first().click()
    expect(calls.some(url => url.includes('/api/hooks/hook_manager/install?provider=codex'))).toBe(true)
    expect(calls.some(url => url.includes('provider=claude'))).toBe(false)
  })
})
