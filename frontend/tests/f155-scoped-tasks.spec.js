/**
 * Feature #155 R1 — the feature-scoped Tasks page opens on the feature's next
 * step: a head naming the feature and its stage, a primary button dispatched
 * by `next_step.kind`, and creates filed under the feature.
 *
 * Live against the suite's server where the claim is about real rows (the
 * label the server computes, the `feature_id` a create lands with); the
 * `run` and `none` rows of the decision table stub only the head's one read,
 * because building a runnable armed task needs a launchable environment.
 */
import { test, expect } from './auth-fixture.js'
import { TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'

const unique = (label) => `${label} ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`

const SLICED_SPEC = [
  '## Acceptance', '',
  '```gherkin',
  'Scenario: a',
  '  Given a feature',
  '  When it is sliced',
  '  Then one slice is armable',
  '```', '',
  '## Spec', '',
  '| # | Slice | Delivers | Spec refs | Tests | Schema | Depends on | Base | Skill |',
  '|---|---|---|---|---|---|---|---|---|',
  '| S1 | one | a thing | R1 | test_a | — | — | `master` | goal-verified |',
].join('\n')

async function api(request, method, path, data) {
  const { token } = await (await request.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })).json()
  const res = await request[method](`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${token}` }, data,
  })
  expect(res.ok(), `${method} ${path}`).toBeTruthy()
  return res.json()
}

const newFeature = (request, body) =>
  api(request, 'post', '/api/features', { title: unique('Scoped'), ...(body ? { body } : {}) })

function stubHead(page, feature, nextStep) {
  return page.route(new RegExp(`/api/features/${feature.id}/next-step$`), route => route.fulfill({
    json: { id: feature.id, title: feature.title, stage: 'building', project_id: null,
            task_count: 1, next_step: nextStep },
  }))
}

test('test_scoped_tasks_head_shows_next_step_button', async ({ page, request }) => {
  const feature = await newFeature(request, SLICED_SPEC)
  const head = await api(request, 'get', `/api/features/${feature.id}/next-step`)
  expect(head.next_step.kind).toBe('slice')

  await page.goto(`/tasks?feature_id=${feature.id}`)

  const scope = page.getByTestId('tasks-scoped-to')
  await expect(scope.getByTestId('tasks-scope-feature')).toHaveText(`#${feature.id} ${feature.title}`)
  await expect(scope.getByTestId('tasks-scope-stage')).toContainText('spec')
  const primary = scope.getByTestId('tasks-scope-primary')
  await expect(primary).toHaveText(head.next_step.label)
  await expect(primary).toBeInViewport()
  // An empty scope says what comes next instead of only "press c".
  await expect(page.getByTestId('tasks-scope-empty-primary')).toHaveText(head.next_step.label)

  await primary.click()
  await expect(page).toHaveURL(new RegExp(`/features/${feature.id}#slices$`))
})

test.describe('test_scope_head_action_follows_decision_table', () => {
  test('write_spec opens the spec on the feature page', async ({ page, request }) => {
    const feature = await newFeature(request)
    await page.goto(`/tasks?feature_id=${feature.id}`)

    const primary = page.getByTestId('tasks-scope-primary')
    await expect(primary).toHaveText('Write the spec')
    await primary.click()
    await expect(page).toHaveURL(new RegExp(`/features/${feature.id}#feature-spec$`))
  })

  test('arm_slices opens the slice arm sheet', async ({ page, request }) => {
    const feature = await newFeature(request, SLICED_SPEC)
    await api(request, 'post', `/api/features/${feature.id}/transcribe`)
    await page.goto(`/tasks?feature_id=${feature.id}`)

    const primary = page.getByTestId('tasks-scope-primary')
    await expect(primary).toHaveText('Arm 1 slice')
    await primary.click()
    await expect(page).toHaveURL(new RegExp(`/features/${feature.id}#slices$`))
    await expect(page.getByTestId('slice-arm-form')).toBeVisible()
    // The ask is spent: a reload does not open the sheet again.
    await expect(page).not.toHaveURL(/[?&]arm=/)
  })

  test('run on a task target posts /run for that task', async ({ page, request }) => {
    const feature = await newFeature(request)
    await stubHead(page, feature,
      { kind: 'run', label: 'Run the next task', target: '/tasks/987654' })
    const ran = []
    await page.route(/\/api\/tasks\/987654\/run$/, route => {
      ran.push(route.request().method())
      return route.fulfill({ json: { ok: true } })
    })
    await page.goto(`/tasks?feature_id=${feature.id}`)

    const primary = page.getByTestId('tasks-scope-primary')
    await expect(primary).toHaveText('Run the next task')
    await primary.click()
    await expect.poll(() => ran).toEqual(['POST'])
    await expect(page).toHaveURL(new RegExp(`/tasks\\?feature_id=${feature.id}`))
  })

  test('a refused run is shown beside the button', async ({ page, request }) => {
    const feature = await newFeature(request)
    await stubHead(page, feature,
      { kind: 'run', label: 'Run the next task', target: '/tasks/987655' })
    await page.route(/\/api\/tasks\/987655\/run$/, route => route.fulfill({
      status: 409, json: { error: 'blocked by #1', blocking: [1] } }))
    await page.goto(`/tasks?feature_id=${feature.id}`)

    await page.getByTestId('tasks-scope-primary').click()
    await expect(page.getByTestId('tasks-scope-refusal')).toContainText('blocked by #1')
  })

  test('none renders no primary button', async ({ page, request }) => {
    const feature = await newFeature(request)
    await stubHead(page, feature, { kind: 'none', label: '', target: null })
    await page.goto(`/tasks?feature_id=${feature.id}`)

    await expect(page.getByTestId('tasks-scope-feature')).toContainText(feature.title)
    await expect(page.getByTestId('tasks-scope-primary')).toHaveCount(0)
    await expect(page.getByTestId('tasks-scope-new')).toHaveText(`New task in #${feature.id}`)
  })
})

test('test_scoped_new_task_prefills_feature', async ({ page, request }) => {
  const feature = await newFeature(request)
  await page.goto(`/tasks?feature_id=${feature.id}`)
  await expect(page.getByTestId('tasks-scope-feature')).toContainText(feature.title)

  await page.keyboard.press('c')
  await expect(page.getByTestId('task-composer-feature-chosen')).toContainText(`#${feature.id}`)
  await page.getByTestId('task-title').fill(unique('scoped'))
  const created = page.waitForResponse(r => r.url().endsWith('/api/tasks') && r.request().method() === 'POST')
  await page.getByTestId('task-submit').click()
  const { task } = await (await created).json()

  const read = await api(request, 'get', `/api/tasks/${task.id}`)
  expect(read.task.feature_id).toBe(feature.id)
  // The head re-reads after the write, so the empty-scope prompt goes away.
  await expect(page.getByTestId('tasks-scope-empty')).toHaveCount(0)

  // The head's own "New task in #N" files there too.
  await page.getByTestId('tasks-scope-new').click()
  await expect(page.getByTestId('task-composer-feature-chosen')).toContainText(`#${feature.id}`)
})
