/**
 * Feature #120 — the arm sheet offers the launch mode, prefilled per R4b of #119.
 */
import { test, expect } from './auth-fixture.js'

const TASK = {
  id: 61, title: 'restart slice', status: 'armed', priority: 'none', body: '',
  kind: 'task', created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z', tags: [], project_id: null,
  feature_id: null, run_count: 0, images: [], comments: [], deps: [],
  action: 'run', waits_on: null,
  menu: ['disarm', 'arm', 'edit', 'deps', 'mark_done', 'archive', 'delete'],
}

async function openArmSheet(page, { armed, detached_detail = '', runs = [] }) {
  const posted = []
  await page.route('**/api/tasks/61/runs', (route) => route.fulfill({ json: { runs } }))
  await page.route('**/api/tasks/61/arm-proposal', (route) =>
    route.fulfill({ status: 404, json: { error: 'not a slice' } }))
  await page.route(/\/api\/tasks\/61(\?|$)/, (route) =>
    route.fulfill({ json: { task: TASK, feature: null, armed } }))
  await page.route('**/api/tasks/61/arm', (route) => {
    posted.push(route.request().postDataJSON())
    return route.fulfill({ json: { task: TASK, armed } })
  })
  await page.route('**/api/agent-runs/launch-options', (route) =>
    route.fulfill({ json: {
      enabled: true, cwds: [], models: ['opus'], permission_modes: ['default'],
      efforts: [], default_permission_mode: 'default', detached_detail,
    } }))
  await page.goto('/tasks/61')
  await page.getByTestId('task-detail-actions').getByTestId('task-menu').click()
  await page.getByTestId('task-verb-arm').click()
  await expect(page.getByTestId('task-arm-form')).toBeVisible()
  return posted
}

test('test_arm_sheet_offers_launch_mode', async ({ page }) => {
  const armed = { status: 'armed', attempt: 1, verify_cmd: null, autorun: false,
                  params: { launch: 'tmux' } }
  const posted = await openArmSheet(page, { armed })

  // R4b source 1: the pending attempt's launch mode.
  await expect(page.getByTestId('task-arm-launch')).toContainText('tmux')
  await page.getByTestId('task-arm-submit').click()
  await expect.poll(() => posted.length).toBe(1)
  expect(posted[0].launch).toBe('tmux')
})

test('test_arm_sheet_launch_defaults_to_sdk', async ({ page }) => {
  const armed = { status: 'armed', attempt: 1, verify_cmd: null, autorun: false,
                  params: {} }
  const posted = await openArmSheet(page, { armed, detached_detail: 'bridge disabled' })

  await expect(page.getByTestId('task-arm-launch')).toContainText('SDK')
  await expect(page.getByTestId('task-arm-launch-off')).toHaveCount(0)
  await page.getByTestId('task-arm-launch').click()
  await page.getByRole('option', { name: /tmux/ }).click()
  await expect(page.getByTestId('task-arm-launch-off')).toContainText('bridge disabled')
  await page.getByTestId('task-arm-submit').click()
  await expect.poll(() => posted.length).toBe(1)
  expect(posted[0].launch).toBe('tmux')
})

test('test_arm_sheet_launch_never_comes_from_prefs', async ({ page }) => {
  // A remembered pref naming tmux must never choose the launch mode (D1).
  await page.addInitScript(() => localStorage.setItem(
    'regin.tasks.arm.last', JSON.stringify({ model: 'opus', launch: 'tmux' })))
  await openArmSheet(page, { armed: null })
  await expect(page.getByTestId('task-arm-launch')).toContainText('SDK')
})

test('test_arm_sheet_launch_prefills_from_last_attempt', async ({ page }) => {
  const runs = [{ id: 9, attempt: 1, status: 'failed', trace_id: 'x', live: false,
                  params: { launch: 'tmux' }, verify_cmd: null }]
  await openArmSheet(page, { armed: null, runs })
  await expect(page.getByTestId('task-arm-launch')).toContainText('tmux')
})
