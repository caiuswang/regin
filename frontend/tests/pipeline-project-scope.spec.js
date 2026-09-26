/**
 * Feature #73 · R4 + invariant 5 — the project scope, as the browser sees it.
 *
 * The API half of these rules is already pinned (`tests/pipeline/
 * test_shell_counts.py`, `tests/defects/test_web_list_query.py`,
 * `tests/features/test_index_paging.py`): the stores know what `none` means
 * and no row is invisible under every scope. What nobody could see from a
 * request is the join — the shell owns `?project=` and each tab has to *ask*
 * under it. A tab that reads the axis under another name, or drops it on the
 * next control change, shows a correct answer to a question the operator did
 * not ask, and the header above it still says which scope it is in.
 *
 * So every assertion here is about the request the page sent, captured from
 * the page's own routes. The rows are stubbed: this is a test about the query,
 * never about the ledger.
 */
import { test, expect } from './auth-fixture.js'

const COUNTS = { tasks: 7, features: 3, defects: 5 }
const REPOS = [{ id: 7, name: 'regin' }, { id: 9, name: 'atlas' }]

// A feature filed in project 7, and one filed nowhere. "Nowhere" is a scope,
// not an absence, so both rows have to be reachable.
const FILED = {
  id: 242, title: 'Filed row', origin: 'native', external_ref: null,
  stage: 'shipped', blurb: '', rounds: 1, sessions: 1, cost_usd: 1,
  gaps: {}, defects: { total: 0, open: 0, escapes: 0 },
  last_activity: '2026-09-01 10:00:00',
}
const UNFILED = { ...FILED, id: 206, title: 'Loose row' }

function defect(over = {}) {
  return {
    id: 1, feature_id: null, feature_title: null, symptom: 'the gutter folds',
    status: 'open', round: 1, blamed_against_state: null, missing_check: null,
    suspect_paths: [], fix_provider: null, fix_external_key: null,
    opened_at: '2026-09-14 12:00:00', closed_at: null, ...over,
  }
}
// D follows its feature's project; an unattributed defect has no project at
// all — the two rows R4's defect column is written about.
const FOLLOWER = defect({ id: 2, feature_id: 81, feature_title: 'The ledger',
                          symptom: 'the rail clips',
                          blamed_against_state: 'started' })
const LOOSE = defect()

async function stubShell(page) {
  await page.route('**/api/pipeline/counts*', (route) =>
    route.fulfill({ json: { counts: COUNTS } }))
  await page.route('**/api/repos', (route) =>
    route.fulfill({ json: { repos: REPOS } }))
}

/**
 * Answers `/api/features` the way `lib/features/index.py` does — `none` is
 * the rows with no project, an id is that project's rows — and records every
 * query, so a test cannot pass by the page rendering a scope it never asked
 * for.
 */
async function stubFeatures(page) {
  const seen = []
  await page.route(/\/api\/features(\?.*)?$/, (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const params = new URL(route.request().url()).searchParams
    seen.push(params)
    const scope = params.get('project_id')
    const items = scope === 'none' ? [UNFILED]
      : scope === '7' ? [FILED]
        : scope ? [] : [FILED, UNFILED]
    return route.fulfill({
      json: {
        items,
        pagination: { strategy: 'offset', total: items.length, page: 0,
                      size: 50, has_next: false, has_prev: false },
        total: 2, coverage: null, unattributed_defects: 0,
      },
    })
  })
  return seen
}

/** The same, for `/api/defects` — whose scope parameter is spelled `project`. */
async function stubDefects(page) {
  const seen = []
  await page.route('**/api/defects*', (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const params = new URL(route.request().url()).searchParams
    seen.push(params)
    const scope = params.get('project')
    // An unattributed defect has no project, so it rides `all` and `none` and
    // no named scope. An attributed one follows its feature's.
    const items = scope === 'none' ? [LOOSE]
      : scope === '7' ? [FOLLOWER]
        : scope ? [] : [FOLLOWER, LOOSE]
    return route.fulfill({
      json: {
        items, scope: 'all',
        pagination: { strategy: 'offset', total: items.length, page: 0,
                      size: 50, has_next: false, has_prev: false },
      },
    })
  })
  return seen
}

const last = (seen) => seen[seen.length - 1] || new URLSearchParams()

// The shell's selector is a Reka combobox portalled to the end of <body>, so
// choosing is a click on the trigger and a click on the option.
async function chooseScope(page, label) {
  await page.getByTestId('pipeline-project').click()
  await page.getByRole('option', { name: label, exact: true }).click()
}

test.beforeEach(async ({ page }) => {
  // The scope is restored from localStorage on a bare path, which is exactly
  // the axis under test — so each test starts from a shell that has no
  // opinion of its own.
  await page.addInitScript(() =>
    localStorage.removeItem('regin.pipeline.project'))
  await stubShell(page)
})

test('test_feature_project_null_listed_under_none_scope', async ({ page }) => {
  const seen = await stubFeatures(page)

  await page.goto('/features?project=none')
  await expect(page.getByText('Loose row')).toBeVisible()
  await expect(page.getByText('Filed row')).toHaveCount(0)
  expect(last(seen).get('project_id')).toBe('none')
  // The control above the rows says which scope they are: a list narrowed by
  // a header that still reads "All projects" is the defect, not the rows.
  await expect(page.getByTestId('pipeline-project')).toContainText('No project')
})

test('test_feature_project_null_listed_under_none_scope — chosen, not typed',
  async ({ page }) => {
    // The same rule reached the way an operator reaches it. The URL form can
    // pass on a tab that reads the axis once at mount; this one cannot.
    const seen = await stubFeatures(page)

    await page.goto('/features')
    await expect(page.getByText('Filed row')).toBeVisible()

    await chooseScope(page, 'No project')
    await expect(page).toHaveURL(/project=none/)
    await expect(page.getByText('Loose row')).toBeVisible()
    await expect(page.getByText('Filed row')).toHaveCount(0)
    expect(last(seen).get('project_id')).toBe('none')
  })

test('test_defect_project_follows_feature', async ({ page }) => {
  const seen = await stubDefects(page)

  await page.goto('/defects?project=7')
  await expect(page.getByText('the rail clips')).toBeVisible()
  await expect(page.getByText('the gutter folds')).toHaveCount(0)
  expect(last(seen).get('project')).toBe('7')
})

test('test_unattributed_defect_has_no_project', async ({ page }) => {
  const seen = await stubDefects(page)

  // Under "All" it is there, and under "No project" it is still there: having
  // no project is not the same as being filtered out of the page.
  await page.goto('/defects')
  await expect(page.getByText('the gutter folds')).toBeVisible()
  expect(last(seen).get('project')).toBeNull()

  await chooseScope(page, 'No project')
  await expect(page.getByText('the gutter folds')).toBeVisible()
  expect(last(seen).get('project')).toBe('none')

  // And under a named project it is not, because it is in none of them.
  await chooseScope(page, 'regin')
  await expect(page.getByText('the gutter folds')).toHaveCount(0)
  expect(last(seen).get('project')).toBe('7')
})

test('test_no_row_is_invisible_under_every_scope', async ({ page }) => {
  // The store half of invariant 5 is pinned in pytest. Its browser half is
  // that the shell *offers* every scope a row can be in — a selector missing
  // "No project" would leave the unfiled rows reachable only by hand-editing
  // the address bar, which is invisibility with extra steps.
  await stubFeatures(page)
  await page.goto('/features')
  await expect(page.getByTestId('pipeline-project')).toBeVisible()

  await page.getByTestId('pipeline-project').click()
  const offered = (await page.getByRole('option').allInnerTexts())
    .map(text => text.trim())
  // Both sentinels and every registered project. Asserted as membership
  // rather than as a sequence: the order of the repo registry is not a rule
  // this test is about, and pinning it would fail on a rename.
  for (const scope of ['All projects', 'No project', ...REPOS.map(r => r.name)]) {
    expect(offered).toContain(scope)
  }
})

test('the default scope is asked for by saying nothing, never by name',
  async ({ page }) => {
    // `all` is the absence of a narrowing, and both endpoints refuse the
    // literal (`lib/defects/query.py::parse_project`). A hand-typed or stale
    // `?project=all` therefore has to reach the server as no parameter at
    // all, or the default scope renders a 400 under a header reading "All
    // projects".
    const defects = await stubDefects(page)
    await page.goto('/defects?project=all')
    await expect(page.getByText('the gutter folds')).toBeVisible()
    expect(last(defects).get('project')).toBeNull()

    const features = await stubFeatures(page)
    await page.goto('/features?project=all')
    await expect(page.getByText('Filed row')).toBeVisible()
    expect(last(features).get('project_id')).toBeNull()
  })

test('a narrowing does not widen the scope it was applied inside',
  async ({ page }) => {
    // R1: `project` carries, every other axis is per tab. A control that
    // rebuilds the query from its own axes drops the shell's — and the list
    // silently widens to every project while the header still names one.
    const seen = await stubDefects(page)

    await page.goto('/defects?project=7')
    await expect(page.getByTestId('pipeline-list-toolbar')).toBeVisible()
    await page.getByTestId('list-sort-dir').click()

    await expect(page).toHaveURL(/project=7/)
    await expect.poll(() => last(seen).get('project')).toBe('7')
    await expect(page.getByTestId('pipeline-project')).toContainText('regin')
  })

test('a narrowing does not widen the scope on the features tab either',
  async ({ page }) => {
    const seen = await stubFeatures(page)

    await page.goto('/features?project=7')
    await expect(page.getByText('Filed row')).toBeVisible()
    const search = page.getByLabel(
      'Search features by title, spec or tracker reference')
    await search.fill('ledger')
    await search.press('Enter')

    await expect(page).toHaveURL(/project=7/)
    await expect.poll(() => last(seen).get('project_id')).toBe('7')
  })
