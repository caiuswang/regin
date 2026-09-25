/**
 * The terminal launch kind in the New session dialog
 * (`LaunchKindField.vue` inside `LiveLaunchSheet.vue`).
 *
 * Browser-side contract only: the kind toggle's availability comes from
 * launch-options, a terminal launch posts `kind: "terminal"` without
 * `one_shot` (and with `resume` when a session was picked), and lands on
 * `/terminal/<id>`. The server side is owned by
 * tests/agent_bridge/test_terminal_launch.py.
 */
import { test, expect } from './auth-fixture.js'
import { settle } from './helpers/overflow.js'

const OPTIONS = {
  enabled: true,
  cwds: ['/Users/dev/repo-a'],
  permission_modes: ['default', 'acceptEdits', 'plan'],
  default_permission_mode: 'default',
  default_model: '',
  models: ['claude-opus-5'],
  efforts: ['low', 'high'],
  gating_active: true,
  terminal_available: true,
  terminal_detail: '',
}

async function openDialog(page, overrides = {}) {
  await page.route('**/api/agent-runs/launch-options', (route) =>
    route.fulfill({ json: { ...OPTIONS, ...overrides } }))
  await page.goto('/trace/sessions')
  await settle(page)
  await page.getByTestId('new-session-open').click()
  await settle(page)
}

test('a terminal launch posts kind terminal and opens the browser terminal', async ({ page }) => {
  await openDialog(page)
  let body = null
  await page.route('**/api/agent-runs', async (route) => {
    body = route.request().postDataJSON()
    await route.fulfill({ json: { launched: true, trace_id: 'abcd1234-0000-4000-8000-000000000000', kind: 'terminal' } })
  })
  await page.route('**/api/sessions/*/terminal**', (route) => route.abort())

  await page.getByRole('radio', { name: 'Terminal' }).click()
  await expect(page.getByTestId('live-launch-terminal-note')).toBeVisible()
  await expect(page.getByTestId('live-launch-oneshot')).toBeDisabled()
  await page.getByTestId('live-launch-prompt').fill('hi')
  await page.getByTestId('live-launch-go').click()

  await expect(page).toHaveURL(/\/terminal\/abcd1234-0000-4000-8000-000000000000$/)
  expect(body.kind).toBe('terminal')
  expect(body.prompt).toBe('hi')
  expect(body.one_shot).toBeUndefined()
  expect(body.resume).toBeUndefined()
})

test('an unavailable terminal kind is disabled with the server reason', async ({ page }) => {
  await openDialog(page, { terminal_available: false, terminal_detail: 'terminal disabled' })

  await expect(page.getByRole('radio', { name: 'Terminal' })).toBeDisabled()
  await expect(page.getByTestId('live-launch-terminal-why')).toContainText('terminal disabled')
  await expect(page.getByTestId('live-launch-go')).toBeEnabled()
})

test('an SDK-less install still offers the terminal, preselected', async ({ page }) => {
  await openDialog(page, { enabled: false })

  await expect(page.getByTestId('live-launch-disabled')).toHaveCount(0)
  await expect(page.getByRole('radio', { name: 'Terminal' })).toBeChecked()
  await expect(page.getByRole('radio', { name: 'SDK run' })).toBeDisabled()
})

test('with neither kind available the form explains itself', async ({ page }) => {
  await openDialog(page, { enabled: false, terminal_available: false, terminal_detail: 'bridge disabled' })

  await expect(page.getByTestId('live-launch-disabled')).toContainText('bridge disabled')
  await expect(page.getByTestId('live-launch-go')).toHaveCount(0)
})

test('a terminal launch can continue a picked session in a pane', async ({ page }) => {
  await openDialog(page)
  const child = 'feed0000-0000-4000-8000-000000000000'
  let body = null
  await page.route('**/api/agent-runs', async (route) => {
    body = route.request().postDataJSON()
    await route.fulfill({ json: { launched: true, trace_id: child, kind: 'terminal' } })
  })
  await page.route('**/api/agent-runs/resumable*', (route) => route.fulfill({ json: { sessions: [{
    session_id: child, title: 'a terminal session', cwd: '/Users/dev/repo-a',
    last_seen: '2026-01-01T10:00:00', prompts: 3, kind: 'session', run_trace_id: '',
  }] } }))
  await page.route('**/api/sessions/*/terminal**', (route) => route.abort())

  await page.getByRole('radio', { name: 'Terminal' }).click()
  await page.getByTestId('live-launch-resume-open').click()
  await expect(page.getByTestId('live-resume-row')).toContainText('keeps its trace')
  await page.getByTestId('live-resume-row').click()
  await expect(page.getByTestId('live-launch-resume-label')).toContainText('terminal session · keeps its trace')
  await page.getByTestId('live-launch-go').click()

  await expect(page).toHaveURL(new RegExp(`/terminal/${child}$`))
  expect(body.kind).toBe('terminal')
  expect(body.resume).toBe(child)
  expect(body.cwd).toBe('/Users/dev/repo-a')
  expect(body.worktree).toBeUndefined()
})
