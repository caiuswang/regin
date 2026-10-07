/**
 * Feature #140 S6 — the /live launch sheet picks a provider the way the arm
 * sheets do (S4): launch-options' `providers` drive the picker, the fields
 * follow what the picked provider declares, and the body carries `provider`
 * with nothing it does not declare (Invariant 4). A resume pick runs on the
 * session's own provider, which the picker row names.
 *
 * Browser side only; the route is pinned in tests/web/test_live_launch_provider.py.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'
import { settle } from './helpers/overflow.js'

test.use({ viewport: { width: 375, height: 667 } })

const CLAUDE = {
  id: 'claude', display_name: 'Claude Code', binary: 'claude', available: true,
  launch_modes: ['sdk', 'tmux'], fields: ['model', 'permission_mode', 'effort'],
  labels: { sdk: 'Agent SDK', tmux: 'tmux' }, models: ['claude-opus-5'], default_model: '',
}
const KIMI = {
  id: 'kimi', display_name: 'Kimi Code', binary: 'kimi', available: true,
  launch_modes: ['sdk'], fields: ['model'], labels: { sdk: 'ACP' },
  models: [], default_model: '',
}
const OTHER = {
  id: 'othercli', display_name: 'Other CLI', binary: 'othercli', available: false,
  launch_modes: ['sdk'], fields: [], labels: {}, models: [], default_model: '',
}

const OPTIONS = {
  enabled: true, cwds: ['/Users/dev/repo-a'],
  permission_modes: ['default', 'acceptEdits'], default_permission_mode: 'default',
  default_model: '', models: ['claude-opus-5'], efforts: ['high'], default_effort: '',
  gating_active: false, terminal_available: true,
}

const KIMI_ROW = {
  session_id: 'session_kimi0001', title: 'a kimi session', cwd: '/Users/dev/repo-a',
  last_seen: '2026-10-01T10:00:00', prompts: 2, kind: 'session', run_trace_id: '',
  model: '', provider: 'kimi',
}

async function postSession(page) {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const res = await page.request.post('/api/session-spans', {
    data: [{ trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
      start_time: new Date().toISOString(),
      attributes: { text: `PROVIDER_FIXTURE_${sfx}`, is_test: true } }],
  })
  expect(res.ok()).toBeTruthy()
  return traceId
}

async function openSheet(page, { providers = [CLAUDE, KIMI, OTHER], rows = [] } = {}) {
  const posted = []
  await page.route('**/api/agent-runs', async (route) => {
    posted.push(route.request().postDataJSON())
    await route.fulfill({ json: { launched: true, trace_id: 'session_fake0001' } })
  })
  await page.route('**/api/agent-runs/launch-options', route =>
    route.fulfill({ json: { ...OPTIONS, providers } }))
  await page.route('**/api/agent-runs/resumable*', route =>
    route.fulfill({ json: { sessions: rows } }))
  const traceId = await postSession(page)
  await page.goto(`/live/${traceId}`)
  await settle(page)
  await page.getByTestId('live-launch-btn').click()
  await settle(page)
  return posted
}

async function pick(page, testid, label) {
  await page.getByTestId(testid).click()
  await page.getByRole('option', { name: label, exact: true }).click()
  await settle(page)
}

test('test_live_launch_sheet_offers_provider', async ({ page }) => {
  const posted = await openSheet(page)

  const picker = page.getByTestId('live-launch-provider')
  await expect(picker).toContainText('Claude Code')
  await picker.click()
  await expect(page.getByRole('option', { name: 'Kimi Code', exact: true })).toBeEnabled()
  await expect(page.getByRole('option', { name: 'Other CLI — othercli not on PATH' }))
    .toHaveAttribute('data-disabled', /.*/)
  await page.getByRole('option', { name: 'Kimi Code', exact: true }).click()
  await settle(page)

  // Kimi declares model only: Claude's vocabulary is not offered.
  await expect(page.getByTestId('live-launch-mode')).toHaveCount(0)
  await expect(page.getByTestId('live-launch-effort')).toHaveCount(0)
  await page.getByTestId('live-launch-model').click()
  await expect(page.getByRole('option', { name: 'claude-opus-5' })).toHaveCount(0)
  await page.getByRole('option', { name: 'custom…' }).click()
  await page.getByTestId('live-launch-model-custom').fill('kimi-code/k3')
  await page.getByTestId('live-launch-prompt').fill('hello kimi')
  await page.getByTestId('live-launch-go').click()
  await settle(page)

  expect(posted).toHaveLength(1)
  expect(posted[0].provider).toBe('kimi')
  expect(posted[0].model).toBe('kimi-code/k3')
  expect(posted[0]).not.toHaveProperty('permission_mode')
  expect(posted[0]).not.toHaveProperty('effort')
})

test('switching provider drops the model picked for the other CLI', async ({ page }) => {
  const posted = await openSheet(page)

  await pick(page, 'live-launch-model', 'claude-opus-5')
  await pick(page, 'live-launch-provider', 'Kimi Code')
  await page.getByTestId('live-launch-go').click()
  await settle(page)

  expect(posted[0].provider).toBe('kimi')
  expect(posted[0]).not.toHaveProperty('model')
})

test('a claude launch body is what it was before providers', async ({ page }) => {
  const posted = await openSheet(page)

  await pick(page, 'live-launch-model', 'claude-opus-5')
  await pick(page, 'live-launch-mode', 'acceptEdits')
  await page.getByTestId('live-launch-go').click()
  await settle(page)

  // No provider key unless one was picked: the server defaults to claude (D1).
  expect(posted[0]).not.toHaveProperty('provider')
  expect(posted[0].model).toBe('claude-opus-5')
  expect(posted[0].permission_mode).toBe('acceptEdits')
})

test('a resume pick runs on the session\'s own provider', async ({ page }) => {
  const posted = await openSheet(page, { rows: [KIMI_ROW] })

  await page.getByTestId('live-launch-resume-open').click()
  await settle(page)
  await page.getByTestId('live-resume-row').click()
  await settle(page)

  const picker = page.getByTestId('live-launch-provider')
  await expect(picker).toContainText('Kimi Code')
  await expect(picker).toBeDisabled()
  await expect(page.getByTestId('live-launch-mode')).toHaveCount(0)
  await page.getByTestId('live-launch-go').click()
  await settle(page)

  expect(posted[0].resume).toBe('session_kimi0001')
  expect(posted[0].provider).toBe('kimi')
})

test('dropping a resume pick restores the provider it overwrote', async ({ page }) => {
  await openSheet(page, { rows: [KIMI_ROW] })

  await page.getByTestId('live-launch-resume-open').click()
  await settle(page)
  await page.getByTestId('live-resume-row').click()
  await settle(page)
  await page.getByTestId('live-launch-resume-clear').click()
  await settle(page)

  await expect(page.getByTestId('live-launch-provider')).toContainText('Claude Code')
  await expect(page.getByTestId('live-launch-provider')).toBeEnabled()
  await expect(page.getByTestId('live-launch-mode')).toBeVisible()
})

test('a terminal launch is claude\'s pane, so it offers no provider', async ({ page }) => {
  await openSheet(page)

  await pick(page, 'live-launch-provider', 'Kimi Code')
  await page.getByTestId('live-launch-kind').getByText('Terminal', { exact: true }).click()
  await settle(page)

  await expect(page.getByTestId('live-launch-provider')).toHaveCount(0)
  await expect(page.getByTestId('live-launch-mode')).toBeVisible()
})
