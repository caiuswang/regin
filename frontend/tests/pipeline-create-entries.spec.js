/**
 * Create entries reachable from wherever a feature is named (feature #142).
 *
 * Live, against the suite's own server: the claims are about what a create
 * lands as (`feature_id`) and what the open page shows afterwards without a
 * reload, which a stub would answer by construction. Titles are unique per
 * test because the chromium and pixel projects share one database.
 */
import { test, expect } from './auth-fixture.js'
import { TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'
import { contentOverflow } from './helpers/overflow.js'

const unique = (label) => `${label} ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`

async function api(request, method, path, data) {
  const { token } = await (await request.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })).json()
  const res = await request[method](`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${token}` }, data,
  })
  expect(res.ok(), `${method} ${path}`).toBeTruthy()
  return res.json()
}

const newFeature = (request) => api(request, 'post', '/api/features', { title: unique('Entry') })

// Classic draws the stage as a select, pixel as a row of radio keys.
async function pickStage(page, label) {
  const field = page.getByTestId('task-composer-stage')
  const key = field.getByRole('radio', { name: label })
  if (await key.count()) return key.click()
  await field.click()
  await page.getByRole('option').filter({ hasText: label }).click()
}

const scrollTop = (page) => page.evaluate(() => document.querySelector('.content-scroll').scrollTop)

async function openLedger(page, feature) {
  await page.goto(`/features/${feature.id}`)
  await expect(page.getByTestId('feature-section-nav')).toBeVisible()
  await expect(page.getByTestId('feature-defects')).toBeVisible()
}

async function asViewer(page) {
  await page.addInitScript(() => {
    const raw = localStorage.getItem('regin_auth_user')
    if (raw) {
      const user = JSON.parse(raw)
      user.role = 'viewer'
      localStorage.setItem('regin_auth_user', JSON.stringify(user))
    }
  })
}

test('test_header_new_defect_opens_prefilled_without_scrolling', async ({ page, request }) => {
  const feature = await newFeature(request)
  await openLedger(page, feature)
  expect(await scrollTop(page)).toBe(0)

  await page.getByTestId('feature-new-defect').click()
  await expect(page.getByTestId('new-defect-feature-chosen')).toContainText(`#${feature.id}`)
  expect(await scrollTop(page)).toBe(0)
})

test('test_strip_report_from_the_foot_lands_without_reload', async ({ page, request }) => {
  const feature = await newFeature(request)
  await page.setViewportSize({ width: 1280, height: 520 })
  await openLedger(page, feature)
  await page.evaluate(() => { window.__noReload = true })
  await page.evaluate(() => {
    const pane = document.querySelector('.content-scroll')
    pane.scrollTop = pane.scrollHeight
  })
  await expect.poll(() => scrollTop(page)).toBeGreaterThan(0)

  const strip = page.getByTestId('section-nav-defects')
  await expect(strip).toContainText('0/0')
  const report = page.getByTestId('section-nav-report-defect')
  await expect(report).toBeInViewport()
  await report.click()

  const symptom = unique('strip symptom')
  await page.getByTestId('new-defect-symptom').fill(symptom)
  await page.getByTestId('new-defect-submit').click()

  await expect(page.getByTestId('feature-defects-list')).toContainText(symptom)
  await expect(strip).toContainText('1/1')
  expect(await page.evaluate(() => window.__noReload)).toBe(true)
})

test('test_c_opens_the_prefilled_dialog_but_types_in_the_editor', async ({ page, request }) => {
  const feature = await newFeature(request)
  await openLedger(page, feature)

  await page.locator('body').click({ position: { x: 5, y: 5 } })
  await page.keyboard.press('c')
  await expect(page.getByTestId('new-defect-feature-chosen')).toContainText(`#${feature.id}`)
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('new-defect')).toHaveCount(0)

  await page.getByTestId('feature-spec-edit').click()
  const editor = page.getByTestId('feature-spec-input')
  await editor.click()
  await page.keyboard.press('End')
  await page.keyboard.type('c')
  await expect(editor).toHaveValue(/c$/)
  await expect(page.getByTestId('new-defect')).toHaveCount(0)
})

test('test_strip_new_task_lands_on_the_feature', async ({ page, request }) => {
  const feature = await newFeature(request)
  await openLedger(page, feature)

  await page.getByTestId('section-nav-new-task').click()
  await expect(page.getByTestId('task-composer-feature-chosen')).toContainText(`#${feature.id}`)
  const title = unique('t')
  await page.getByTestId('task-title').fill(title)
  const created = page.waitForResponse(r => r.url().endsWith('/api/tasks') && r.request().method() === 'POST')
  await page.getByTestId('task-submit').click()
  const { task } = await (await created).json()

  const read = await api(request, 'get', `/api/tasks/${task.id}`)
  expect(read.task.feature_id).toBe(feature.id)
  await expect(page.getByTestId('feature-tasks-list')).toContainText(title)
  await expect(page.getByTestId('section-nav-tasks')).toContainText('0/1')
})

test('test_clearing_the_chip_files_to_triage', async ({ page, request }) => {
  const feature = await newFeature(request)
  await openLedger(page, feature)

  await page.getByTestId('feature-new-defect').click()
  await page.getByTestId('new-defect-feature-clear').click()
  await page.getByTestId('new-defect-symptom').fill(unique('triage'))
  const created = page.waitForResponse(r => r.url().endsWith('/api/defects') && r.request().method() === 'POST')
  await page.getByTestId('new-defect-submit').click()
  const { defect } = await (await created).json()
  expect(defect.feature_id).toBeNull()
})

test('test_task_page_reports_from_its_header', async ({ page, request }) => {
  const feature = await newFeature(request)
  const { task } = await api(request, 'post', '/api/tasks', { title: unique('step'), feature_id: feature.id })
  await page.goto(`/tasks/${task.id}`)
  const button = page.getByTestId('task-detail-report-defect')
  await expect(button).toBeVisible()
  const before = await scrollTop(page)

  await button.click()
  await expect(page.getByTestId('new-defect-feature-chosen')).toContainText(`#${feature.id}`)
  expect(await scrollTop(page)).toBe(before)
})

test('test_features_list_expanded_row_reports_prefilled', async ({ page, request }) => {
  const feature = await newFeature(request)
  await page.goto(`/features?stage=spec&q=${encodeURIComponent(feature.title)}`)
  await page.getByTestId(`feature-toggle-${feature.id}`).click()
  await page.getByTestId(`feature-expansion-report-${feature.id}`).click()
  await expect(page.getByTestId('new-defect-feature-chosen')).toContainText(`#${feature.id}`)
})

test('test_features_list_expanded_row_creates_a_task_on_the_feature', async ({ page, request }) => {
  const feature = await newFeature(request)
  await page.goto(`/features?stage=spec&q=${encodeURIComponent(feature.title)}`)
  await page.getByTestId(`feature-toggle-${feature.id}`).click()
  await page.getByTestId(`feature-expansion-new-task-${feature.id}`).click()
  await expect(page.getByTestId('task-composer-feature-chosen')).toContainText(`#${feature.id}`)

  await page.getByTestId('task-title').fill(unique('from the row'))
  const created = page.waitForResponse(r => r.url().endsWith('/api/tasks') && r.request().method() === 'POST')
  await page.getByTestId('task-submit').click()
  const { task } = await (await created).json()
  expect((await api(request, 'get', `/api/tasks/${task.id}`)).task.feature_id).toBe(feature.id)
})

test('test_a_stage_picked_for_one_feature_does_not_follow_to_another', async ({ page, request }) => {
  const tag = unique('pair')
  const a = await api(request, 'post', '/api/features', { title: `${tag} A` })
  const b = await api(request, 'post', '/api/features', { title: `${tag} B` })
  await page.goto(`/features?stage=spec&q=${encodeURIComponent(tag)}`)

  await page.getByTestId(`feature-toggle-${a.id}`).click()
  await page.getByTestId(`feature-expansion-new-task-${a.id}`).click()
  await pickStage(page, 'Spec')
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('task-composer')).toHaveCount(0)

  await page.getByTestId(`feature-toggle-${b.id}`).click()
  await page.getByTestId(`feature-expansion-new-task-${b.id}`).click()
  await expect(page.getByTestId('task-composer-feature-chosen')).toContainText(`#${b.id}`)
  await page.getByTestId('task-title').fill(unique('on B'))
  const created = page.waitForResponse(r => r.url().endsWith('/api/tasks') && r.request().method() === 'POST')
  await page.getByTestId('task-submit').click()
  const sent = (await created).request().postDataJSON()
  expect(sent.feature_id).toBe(b.id)
  expect(sent).not.toHaveProperty('kind')
})

test('test_defects_card_reports_through_the_shell_dialog', async ({ page, request }) => {
  const feature = await newFeature(request)
  await openLedger(page, feature)
  await page.evaluate(() => { window.__noReload = true })

  await page.getByTestId('feature-report-defect').click()
  await expect(page.getByTestId('new-defect-feature-chosen')).toContainText(`#${feature.id}`)
  await expect(page.getByTestId('new-defect-paths')).toBeVisible()
  await expect(page.getByTestId('feature-defects').locator('form')).toHaveCount(0)

  const symptom = unique('card symptom')
  await page.getByTestId('new-defect-symptom').fill(symptom)
  await page.getByTestId('new-defect-submit').click()

  await expect(page.getByTestId('flash').filter({ hasText: `on feature #${feature.id}` })).toBeVisible()
  await expect(page.getByTestId('feature-defects-list')).toContainText(symptom)
  await expect(page.getByTestId('section-nav-defects')).toContainText('1/1')
  expect(await page.evaluate(() => window.__noReload)).toBe(true)
})

test('test_tasks_card_new_task_opens_the_composer_with_a_stage', async ({ page, request }) => {
  const feature = await newFeature(request)
  await openLedger(page, feature)

  await page.getByTestId('feature-new-task').click()
  const composer = page.getByTestId('task-composer')
  await expect(page.getByTestId('task-composer-feature-chosen')).toContainText(`#${feature.id}`)
  await expect(composer.locator('#task-body')).toBeVisible()
  await expect(page.getByTestId('task-composer-priority')).toBeVisible()
  await expect(page.getByTestId('feature-tasks').locator('form')).toHaveCount(0)

  const title = unique('spec step')
  await page.getByTestId('task-title').fill(title)
  await pickStage(page, 'Spec')
  const created = page.waitForResponse(r => r.url().endsWith('/api/tasks') && r.request().method() === 'POST')
  await page.getByTestId('task-submit').click()
  const { task } = await (await created).json()

  const read = (await api(request, 'get', `/api/tasks/${task.id}`)).task
  expect(read.feature_id).toBe(feature.id)
  expect(read.kind).toBe('spec')
  await expect(page.getByTestId('feature-tasks-list')).toContainText(title)
})

test('test_expanded_row_lists_a_defect_filed_elsewhere_without_reload', async ({ page, request }) => {
  const feature = await newFeature(request)
  await page.goto(`/features?stage=spec&q=${encodeURIComponent(feature.title)}`)
  await page.getByTestId(`feature-toggle-${feature.id}`).click()
  await expect(page.getByTestId(`feature-expansion-${feature.id}`)).toContainText('No defect is blamed')
  await page.evaluate(() => { window.__noReload = true })

  const { defect } = await api(request, 'post', '/api/defects', { symptom: unique('elsewhere'), feature_id: feature.id })
  await expect(page.getByTestId(`feature-defect-${defect.id}`)).toBeVisible()
  expect(await page.evaluate(() => window.__noReload)).toBe(true)
})

test('test_a_tasks_tab_create_after_the_feature_page_opens_empty', async ({ page, request }) => {
  const feature = await newFeature(request)
  await openLedger(page, feature)
  await page.getByTestId('section-nav-new-task').click()
  await expect(page.getByTestId('task-composer-feature-chosen')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('task-composer')).toHaveCount(0)

  // In-app navigation: the shell (and its dialog) survives, a goto would not.
  await page.getByTestId('pipeline-tab-tasks').click()
  await expect(page.getByTestId('task-new')).toBeVisible()
  await expect(page.getByTestId('feature-new-defect')).toHaveCount(0)
  await page.getByTestId('pipeline-new').click()
  await page.getByTestId('pipeline-new-task').click()
  await expect(page.getByTestId('task-composer')).toBeVisible()
  await expect(page.getByTestId('task-composer-feature-chosen')).toHaveCount(0)
})

for (const width of [1280, 320]) {
  test(`test_operator_controls_visible_and_no_overflow_at_${width}`, async ({ page, request }) => {
    const feature = await newFeature(request)
    await page.setViewportSize({ width, height: 800 })
    await openLedger(page, feature)
    await expect(page.getByTestId('section-nav-report-defect')).toBeVisible()
    await expect(page.getByTestId('section-nav-new-task')).toBeVisible()
    await expect(page.getByTestId('feature-new-defect')).toBeAttached()
    const o = await contentOverflow(page)
    expect(o.scrollWidth, o.offenders.join(', ')).toBeLessThanOrEqual(o.clientWidth + 1)
  })
}

test('test_viewer_sees_no_create_controls', async ({ page, request }) => {
  const feature = await newFeature(request)
  const { task } = await api(request, 'post', '/api/tasks', { title: unique('viewer step'), feature_id: feature.id })
  await asViewer(page)
  await openLedger(page, feature)
  await expect(page.getByTestId('section-nav-report-defect')).toHaveCount(0)
  await expect(page.getByTestId('section-nav-new-task')).toHaveCount(0)
  await expect(page.getByTestId('feature-new-defect')).toHaveCount(0)
  await page.keyboard.press('c')
  await expect(page.getByTestId('new-defect')).toHaveCount(0)

  await page.goto(`/tasks/${task.id}`)
  await expect(page.getByTestId('task-detail-actions')).toBeVisible()
  await expect(page.getByTestId('task-detail-report-defect')).toHaveCount(0)
})
