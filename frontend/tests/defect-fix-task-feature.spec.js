/**
 * Defect #183: a fix task filed from a feature's defect list must show up —
 * and say which defect it fixes — without a reload.
 */
import { test, expect } from './auth-fixture.js'

const LEDGER = {
  id: 242, title: 'Feature workbench', origin: 'native',
  external_ref: null, external_url: null, sync_error: null, body: 'spec',
  stage: { stage: 'build', blurb: '', rounds: 1, claimed_at: null,
           open_defects: 1, escapes: 0 },
  synced_at: null, stage_live: true,
  cost: { usd: 0, sessions: 0, recalled: 0, shared: 0, gaps: {} },
  work_time: { active_work_ms: 0, gaps: {} },
  tokens: { input: 0, output: 0 },
  sessions: { rows: [], gaps: { traceless_runs: 0 } },
  defects: { open: 1, total: 1, escapes: 0 },
  coverage: { declared_sessions: 0, all_sessions: 0, declared_usd: 0, all_usd: 0 },
}
const DEFECT = {
  id: 7, feature_id: 242, feature_title: 'Feature workbench',
  symptom: 'the gutter folds', status: 'open', round: 1,
  blamed_event_kind: 'started', missing_check: null, suspect_paths: [],
  fix_task_id: null, images: [],
  opened_at: '2026-09-14 12:00:00', closed_at: null,
}
const FIX_TASK = {
  id: 143, title: 'the gutter folds', body: '', status: 'idea', kind: 'fix',
  source: 'defect', tags: [], links: [], priority: 'none', priority_rank: 4,
  project_id: null, feature_id: 242, feature_title: 'Feature workbench',
  assignee: null, assignee_name: null, external_id: 'defect:7', created_by: 1,
  created_at: '2026-09-14 10:00:00', updated_at: '2026-09-14 10:00:00',
  images: [], run_count: 0, fixes_defects: [7],
}

test('filing a fix task from the feature page lists it, linked both ways',
  async ({ page }) => {
    const state = { defect: DEFECT, tasks: [] }
    await page.route(/\/api\/features\/\d+$/, (route) =>
      route.request().method() === 'GET'
        ? route.fulfill({ json: LEDGER }) : route.fallback())
    await page.route('**/api/tasks?**', (route) =>
      route.request().method() === 'GET'
        ? route.fulfill({ json: { items: state.tasks, next_cursor: null } })
        : route.fallback())
    await page.route('**/api/defects?feature_id=*', (route) =>
      route.fulfill({ json: { items: [state.defect], scope: 'feature' } }))
    await page.route('**/api/defects/7/fix-item', async (route) => {
      state.defect = { ...DEFECT, fix_task_id: 143 }
      state.tasks = [FIX_TASK]
      await route.fulfill({ json: { defect: state.defect } })
    })

    await page.goto('/features/242')
    await expect(page.getByTestId('feature-tasks-empty')).toBeVisible()

    await page.getByTestId('defect-fix-create-row-7').click()

    const created = page.getByTestId('defect-fix-created-7')
    await expect(created).toContainText('task #143')
    await expect(created.getByRole('link', { name: 'task #143' }))
      .toHaveAttribute('href', '/tasks/143')
    const tasks = page.getByTestId('feature-tasks-list')
    await expect(tasks).toContainText('#143')
    await expect(page.getByTestId('feature-task-fixes-143-7'))
      .toHaveAttribute('href', '/defects/7')
  })

test('the fix task page names the defect it fixes', async ({ page }) => {
  await page.route(/\/api\/tasks\/143$/, (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: {
        task: FIX_TASK, armed: null,
        feature: { id: 242, title: 'Feature workbench', origin: 'native',
                   project_id: null, project_name: null, external_ref: null,
                   stage: LEDGER.stage } } })
      : route.fallback())
  await page.route('**/api/defects?feature_id=*', (route) =>
    route.fulfill({ json: { items: [], scope: 'feature' } }))

  await page.goto('/tasks/143')
  const fixes = page.getByTestId('task-fixes-defects')
  await expect(fixes.getByRole('link', { name: 'defect #7' }))
    .toHaveAttribute('href', '/defects/7')
})
