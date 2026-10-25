/**
 * Feature #177 — "New follow-up" opens the declare dialog at the followed
 * feature's project, falls back to the page's scope when that feature has
 * none, and sends whatever the picker holds at submit.
 *
 * Stubbed: every assertion is about what the dialog starts at and what it
 * sends. The server half (create + first-filing cascade) is pinned in
 * tests/features/test_project_carry.py.
 */
import { test, expect } from './auth-fixture.js'

const REPOS = [
  { id: 7, name: 'regin', path: '/r/regin' },
  { id: 9, name: 'atlas', path: '/r/atlas' },
]

function ledger(over = {}) {
  return {
    id: 242, title: 'Earlier phase', origin: 'native', external_ref: null,
    external_url: null, sync_error: null, body: '',
    project_id: 7, project_name: 'regin',
    stage: { stage: 'building', blurb: 'being built', rounds: 1,
             claimed_at: null, open_defects: 0, escapes: 0 },
    synced_at: null, stage_live: true,
    cost: { usd: 0, sessions: 0, recalled: 0, shared: 0, gaps: {} },
    work_time: { gaps: {} }, tokens: {},
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: { declared_sessions: 0, all_sessions: 0 },
    links: { follows: [], followed_by: [], related: [] },
    ...over,
  }
}

async function stub(page, followed) {
  await page.route('**/api/repos', (route) =>
    route.fulfill({ json: { repos: REPOS } }))
  await page.route(/\/api\/features\/242(\?.*)?$/, (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: followed }) : route.fallback())
  const posts = []
  await page.route(/\/api\/features$/, (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    posts.push(route.request().postDataJSON())
    return route.fulfill({ status: 201, json: ledger({ id: 243, title: 'next' }) })
  })
  return posts
}

async function openFollowUp(page, path) {
  await page.goto(path)
  await page.getByTestId('section-nav-new-follow-up').click()
  const dialog = page.getByTestId('new-feature')
  await expect(dialog.getByTestId('declare-follows')).toContainText('#242')
  return dialog
}

test('test_follow_up_inherits_the_followed_features_project', async ({ page }) => {
  const posts = await stub(page, ledger())
  const dialog = await openFollowUp(page, '/features/242')

  await expect(dialog.getByTestId('declare-project')).toContainText('regin')
  await dialog.getByTestId('declare-title').fill('next')
  await dialog.getByTestId('declare-submit').click()

  await expect.poll(() => posts.length).toBe(1)
  expect(posts[0]).toMatchObject({ follows: 242, project_id: 7 })
})

test('test_follow_up_of_a_projectless_feature_uses_the_scope', async ({ page }) => {
  await stub(page, ledger({ project_id: null, project_name: null }))
  // A detail page has no `?project=`; its scope is the one the shell stored.
  await page.addInitScript(() => localStorage.setItem('regin.pipeline.project', '9'))
  const dialog = await openFollowUp(page, '/features/242')
  await expect(dialog.getByTestId('declare-project')).toContainText('atlas')
})

test('test_follow_up_of_a_projectless_feature_without_scope_starts_empty',
  async ({ page }) => {
    await stub(page, ledger({ project_id: null, project_name: null }))
    const dialog = await openFollowUp(page, '/features/242')
    await expect(dialog.getByTestId('declare-project')).toContainText('No project')
  })

test('test_follow_up_sends_the_picked_project_over_the_inherited', async ({ page }) => {
  const posts = await stub(page, ledger())
  const dialog = await openFollowUp(page, '/features/242')

  await expect(dialog.getByTestId('declare-project')).toContainText('regin')
  await dialog.getByTestId('declare-project').click()
  await page.getByRole('option', { name: 'atlas', exact: true }).click()
  await dialog.getByTestId('declare-title').fill('next')
  await dialog.getByTestId('declare-submit').click()

  await expect.poll(() => posts.length).toBe(1)
  expect(posts[0]).toMatchObject({ follows: 242, project_id: 9 })
})
