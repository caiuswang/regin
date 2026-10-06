/**
 * Feature #139, slice S6 — the create dialogs and the arm sheet under the
 * pixel theme. They are the classic components with hooks, so the proof is
 * twofold: the pixel shape the design asks for, and nothing lost against the
 * classic sheet — same fields, same options, same prefill, same request.
 */
import { test, expect } from './auth-fixture.js'
import { TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'
import { pickChoice } from './helpers/choice.js'
import { rowAction } from './helpers/rowAction.js'

const plTheme = page => page.evaluate(() => document.documentElement.getAttribute('data-pl-theme'))
const useTheme = (page, theme) => page.addInitScript(
  (id) => localStorage.setItem('regin_pipeline_theme', id), theme)

async function seedIdea(request, title) {
  const { token } = await (await request.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })).json()
  const res = await request.post(`${API_BASE}/api/tasks`, {
    headers: { Authorization: `Bearer ${token}` }, data: { title },
  })
  if (!res.ok()) throw new Error(`seed: ${res.status()} ${await res.text()}`)
  const body = await res.json()
  return body.task ?? body
}

// The arm sheet's served menus, pinned so both themes are read against the
// same install; the arm itself is answered here so the idea stays an idea.
async function stubArm(page, id) {
  const posted = []
  await page.route('**/api/agent-runs/launch-options', route => route.fulfill({ json: {
    enabled: true, cwds: ['/r/regin', '/r/other'], models: ['opus', 'sonnet'],
    permission_modes: ['default', 'acceptEdits'], efforts: ['high', 'low'],
    default_permission_mode: 'default', detached_detail: '',
  } }))
  await page.route('**/api/agent-runs/worktree-options*', route => route.fulfill({ json: {
    branches: ['main', 'dev'], default_base: 'main', worktrees_root: '/r/wt',
  } }))
  await page.route('**/api/agent-runs/skill-options*', route => route.fulfill({ json: {
    skills: [{ name: 'tdd', source: 'project' }, { name: 'goal-verified', source: 'project' }],
  } }))
  await page.route(new RegExp(`/api/tasks/${id}/runs$`), route => route.fulfill({ json: { runs: [] } }))
  await page.route(new RegExp(`/api/tasks/${id}/arm-proposal$`), route =>
    route.fulfill({ status: 404, json: { error: 'not a slice' } }))
  await page.route(new RegExp(`/api/tasks/${id}/arm$`), (route) => {
    posted.push(route.request().postDataJSON())
    return route.fulfill({ json: { task: { id, status: 'armed' }, armed: { status: 'armed', params: {} } } })
  })
  return posted
}

async function openTasks(page, theme, q = '') {
  await useTheme(page, theme)
  await page.goto(q ? `/tasks?q=${encodeURIComponent(q)}` : '/tasks')
  await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
  await expect.poll(() => plTheme(page)).toBe(theme)
  if (theme === 'pixel') await page.evaluate(() => document.fonts.ready)
}

async function openArm(page, id) {
  await rowAction(page.locator(`#task-${id}`), 'arm')
  const dialog = page.getByTestId('task-arm')
  await expect(dialog.getByTestId('task-arm-form')).toBeVisible()
  return dialog
}

// Everything the sheet offers, read the same way under either theme: the
// testids it carries, each select's prefilled value and its full menu.
async function readSheet(page, dialog) {
  const testids = (await dialog.locator('[data-testid]').evaluateAll(
    els => els.map(e => e.dataset.testid))).filter(t => t !== 'task-arm-launch').sort()
  const selects = {}
  for (const id of ['task-arm-cwd', 'task-arm-mode', 'task-arm-model', 'task-arm-effort']) {
    const trigger = dialog.getByTestId(id)
    const value = (await trigger.textContent()).trim()
    await trigger.click()
    const options = await page.getByRole('listbox').getByRole('option').allTextContents()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('listbox')).toHaveCount(0)
    selects[id] = { value, options: options.map(o => o.trim()) }
  }
  const launch = dialog.getByTestId('task-arm-launch')
  const launchOptions = await launch.getAttribute('role') === 'radiogroup'
    ? await launch.getByRole('radio').evaluateAll(els => els.map(e => e.dataset.value))
    : await (async () => {
      await launch.click()
      const v = await page.getByRole('listbox').locator('[role="option"]').evaluateAll(els => els.map(e => e.dataset.value))
      await page.keyboard.press('Escape')
      return v
    })()
  return { testids, selects, launchOptions }
}

async function fillAndArm(page, dialog) {
  await dialog.getByTestId('live-launch-worktree').check()
  await dialog.getByTestId('live-launch-worktree-branch').fill('feat/pixel-arm')
  await dialog.getByTestId('task-arm-skill').click()
  await page.getByRole('option', { name: 'tdd' }).click()
  await dialog.getByTestId('task-arm-skill-args').fill('--fast')
  await dialog.getByTestId('task-arm-verify').fill('.venv/bin/python -m pytest -q')
  await pickChoice(page, 'task-arm-launch', 'tmux')
  await dialog.getByTestId('task-arm-oneshot').check()
  await dialog.getByTestId('task-arm-submit').click()
}

test.describe('pixel dialogs', () => {
  test('test_new_task_dialog', async ({ page }) => {
    await openTasks(page, 'pixel')
    await page.keyboard.press('c')

    const dialog = page.getByTestId('task-composer')
    await expect(dialog).toBeVisible()
    await expect(dialog).toHaveAttribute('role', 'dialog')
    const heading = dialog.getByRole('heading')
    await expect.poll(() => heading.evaluate(h => h.innerText)).toBe('NEW TASK')
    await expect(heading).toHaveAttribute('data-pl-kicker', 'New quest · lands in ideas')
    await expect(dialog.locator('svg[data-sprite="bulb"]')).toBeVisible()
    await expect(page.getByTestId('task-title')).toBeFocused()

    const priority = page.getByTestId('task-composer-priority')
    await expect(priority).toHaveAttribute('role', 'radiogroup')
    await expect(priority.getByRole('radio')).toHaveCount(5)
    await expect(priority.locator('[role="radio"][aria-checked="true"]')).toHaveAttribute('data-value', 'none')
    await expect(priority.locator('svg[data-sprite^="bars."]')).toHaveCount(5)

    const capture = page.getByTestId('task-submit')
    await expect(capture).toHaveAttribute('data-pl-part', 'key--primary')
    await expect(capture).toBeDisabled()
    await page.getByTestId('task-title').fill('   ')
    await expect(capture).toBeDisabled()

    const title = `Pixel quest ${Date.now()}`
    await page.getByTestId('task-title').fill(title)
    await expect(capture).toBeEnabled()
    const created = page.waitForResponse(r => r.url().endsWith('/api/tasks') && r.request().method() === 'POST')
    await page.getByTestId('task-title').press('Enter')
    const body = await (await created).json()
    const task = body.task ?? body
    expect(task.status).toBe('idea')
    expect(task.priority).toBe('none')
    await expect(dialog).toHaveCount(0)

    await page.goto(`/tasks?q=${encodeURIComponent(title)}`)
    await expect(page.getByTestId('next-step-group-ideas').locator(`#task-${task.id}`)).toBeVisible()
  })

  test('test_arm_dialog_keeps_every_field_of_the_classic_sheet', async ({ page, request }) => {
    const title = `Pixel arm ${Date.now()}`
    const idea = await seedIdea(request, title)
    const posted = await stubArm(page, idea.id)

    await openTasks(page, 'classic', title)
    let dialog = await openArm(page, idea.id)
    const classic = await readSheet(page, dialog)
    await fillAndArm(page, dialog)
    await expect.poll(() => posted.length).toBe(1)
    // The sheet remembers this arm just before it closes; forget it, or the
    // pixel sheet opens prefilled from it and is not the same sheet.
    await expect(dialog).toHaveCount(0)
    await page.evaluate(() => localStorage.removeItem('regin.tasks.arm.last'))
    await openTasks(page, 'pixel', title)
    dialog = await openArm(page, idea.id)

    const heading = dialog.getByRole('heading')
    await expect.poll(() => heading.evaluate(h => h.innerText)).toBe('ARM TASK')
    await expect(heading).toHaveAttribute('data-pl-kicker', `Idea → queue · #${idea.id}`)
    await expect(dialog.locator('svg[data-sprite="bot"]')).toBeVisible()
    await expect(dialog.getByTestId('task-arm-launch')).toHaveAttribute('role', 'radiogroup')
    await expect(dialog.getByTestId('task-arm-launch').getByRole('radio')).toHaveText([/SDK \(default\)/, /tmux/])

    const pixel = await readSheet(page, dialog)
    expect(pixel).toEqual(classic)
    expect(pixel.launchOptions).toEqual(['', 'tmux'])

    // WHERE · AGENT · FINISH, in that order.
    const sections = await dialog.locator('[data-pl-part="dialog-section"]').evaluateAll(
      els => els.map(e => getComputedStyle(e, '::before').content))
    expect(sections).toEqual(['"Where"', '"Agent"', '"Finish"'])

    // The worktree check reveals BRANCH and BASE BRANCH, side by side.
    await expect(dialog.getByTestId('live-launch-worktree-branch')).toHaveCount(0)
    await dialog.getByTestId('live-launch-worktree').check()
    const branch = await dialog.getByTestId('live-launch-worktree-branch').boundingBox()
    const base = await dialog.getByTestId('live-launch-worktree-base').boundingBox()
    expect(Math.abs(branch.y - base.y)).toBeLessThan(2)
    expect(base.x).toBeGreaterThan(branch.x + branch.width)
    await dialog.getByTestId('live-launch-worktree').uncheck()

    // MODEL and EFFORT share a row too.
    const model = await dialog.getByTestId('task-arm-model').boundingBox()
    const effort = await dialog.getByTestId('task-arm-effort').boundingBox()
    expect(Math.abs(model.y - effort.y)).toBeLessThan(2)

    await fillAndArm(page, dialog)
    await expect.poll(() => posted.length).toBe(2)
    expect(posted[1]).toEqual(posted[0])
    expect(posted[0]).toMatchObject({ launch: 'tmux' })
  })

  test('test_choice_fields_under_both_themes', async ({ page, request }) => {
    for (const theme of ['classic', 'pixel']) {
      await openTasks(page, theme)
      await page.keyboard.press('c')
      const title = `Choice ${theme} ${Date.now()}`
      await page.getByTestId('task-title').fill(title)
      await pickChoice(page, 'task-composer-priority', 'high')
      const created = page.waitForResponse(r => r.url().endsWith('/api/tasks') && r.request().method() === 'POST')
      await page.getByTestId('task-submit').click()
      expect((await created).request().postDataJSON().priority).toBe('high')

      const idea = await seedIdea(request, `Launch ${theme} ${Date.now()}`)
      const posted = await stubArm(page, idea.id)
      await openTasks(page, theme, idea.title)
      const dialog = await openArm(page, idea.id)
      await pickChoice(page, 'task-arm-launch', 'tmux')
      await dialog.getByTestId('task-arm-submit').click()
      await expect.poll(() => posted.length).toBe(1)
      expect(posted[0].launch).toBe('tmux')
      await page.unrouteAll({ behavior: 'ignoreErrors' })
    }
  })

  test('the new-feature dialog wears the level chrome', async ({ page }) => {
    await useTheme(page, 'pixel')
    await page.goto('/features')
    await expect.poll(() => plTheme(page)).toBe('pixel')
    await page.keyboard.press('c')
    const dialog = page.getByTestId('new-feature')
    await expect(dialog).toBeVisible()
    await expect.poll(() => dialog.getByRole('heading').evaluate(h => h.innerText)).toBe('DECLARE A FEATURE')
    await expect(dialog.getByRole('heading')).toHaveAttribute('data-pl-kicker', 'New level · lands in spec')
    await expect(dialog.locator('svg[data-sprite="flag"]')).toBeVisible()
    await expect(dialog.getByTestId('mode-declare')).toHaveAttribute('aria-pressed', 'true')
    await expect(dialog.locator('[data-pl-part="key--primary"]')).toHaveCount(1)
    await expect(dialog.getByTestId('declare-submit')).toHaveAttribute('data-pl-part', 'key--primary')
  })

  test('classic dialogs draw no pixel chrome', async ({ page }) => {
    await openTasks(page, 'classic')
    await page.keyboard.press('c')
    const dialog = page.getByTestId('task-composer')
    await expect(dialog).toBeVisible()
    await expect(dialog.locator('svg[data-sprite]')).toHaveCount(0)
    await expect.poll(() => dialog.getByRole('heading').evaluate(h => h.innerText)).toBe('New task')
    const kicker = await dialog.getByRole('heading').evaluate(h => getComputedStyle(h, '::before').content)
    expect(kicker).toBe('none')
    await expect(page.getByTestId('task-composer-priority')).not.toHaveAttribute('role', 'radiogroup')
  })
})
