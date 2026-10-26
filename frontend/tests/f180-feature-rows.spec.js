/**
 * F180 S6 — the feature page's lists share one column grammar: a one-line
 * defect head that opens in place, Slices / Tasks rows on fixed grid tracks,
 * the branch as the Slices footer, and the fix relation marked on both sides.
 *
 * Every read is stubbed. The pixel project runs this file too: a theme may
 * re-ink the rows, never move their columns.
 */
import { test, expect } from './auth-fixture.js'

const FID = 1806
const PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM' +
  'IQAAAABJRU5ErkJggg=='

const SESSIONS = [
  { date: '2026-10-01', trace_id: 'aaaaaaaa11', short_trace: 'aaaaaaaa', title: 'Build the grid',
    display_title: 'Build the grid', cost_usd: 1.5, is_primary: true, provenance: 'run', task_id: 31, round: 1 },
  { date: '2026-10-02', trace_id: 'bbbbbbbb22', short_trace: 'bbbbbbbb', title: 'Declared by hand',
    cost_usd: null, is_primary: true, provenance: 'declared', task_id: null, round: null },
]

function ledger() {
  return {
    id: FID, title: 'Feature rows', origin: 'native', body: '# Spec\n\nShort.',
    external_ref: null, external_url: null, sync_error: null, branch: null,
    project_id: null, project_name: null,
    stage: { stage: 'building', blurb: 'in progress', rounds: 1,
             claimed_at: null, open_defects: 2, escapes: 0 },
    synced_at: null, stage_live: true, verbs: [], ship_check: null,
    next_step: null, slice_tasks: 4, images: [],
    cost: { usd: 1.5, sessions: 2, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 0, gaps: {} },
    tokens: {},
    sessions: { rows: SESSIONS, gaps: {} },
    defects: { open: 2, total: 3, escapes: 0, gaps: {} },
    coverage: { declared_sessions: 2, all_sessions: 2, declared_usd: 1.5, all_usd: 1.5 },
  }
}

function defect(id, over = {}) {
  return {
    id, feature_id: FID, feature_title: 'Feature rows',
    symptom: `symptom ${id}`, status: 'open', round: 1,
    blamed_event_kind: 'started', missing_check: null, suspect_paths: ['frontend/src/x.vue'],
    fix_task_id: null, fix_state: null, fix_drift: false,
    triage_state: null, triage_reason: null, queue_position: null,
    images: [], opened_at: '2026-10-14 12:00:00',
    updated_at: '2026-10-14 12:00:00', closed_at: null, ...over,
  }
}

const DEFECTS = [
  defect(41, {
    symptom: 'The Task column drifts right ![](image:1)\nsecond line of the report\nthird line',
    images: [{ idx: 1, media_type: 'image/png', byte_size: 68 }],
    fix_task_id: 31, fix_state: 'running',
  }),
  defect(42, { symptom: '\n\nNo one is fixing this yet' }),
  defect(40, { status: 'closed', fix_task_id: 32, fix_state: 'done' }),
]

function task(id, over = {}) {
  return {
    id, title: `step ${id}`, body: '', status: 'armed', kind: 'build', source: 'web',
    tags: [], links: [], priority: 'none', priority_rank: 4, project_id: null,
    feature_id: FID, feature_title: 'Feature rows', assignee: null, assignee_name: null,
    external_id: null, created_by: 1, created_at: '2026-10-01 10:00:00',
    updated_at: '2026-10-01 10:00:00', images: [], run_count: 0, waits_on: null,
    blocked_by: [], fixes_defects: [], fixes_open_defects: [], ...over,
  }
}

const TASKS = [
  task(31, { title: 'Fix the overlap', status: 'running', kind: 'fix', run_count: 2,
             fixes_defects: [41], fixes_open_defects: [41] }),
  task(32, { title: 'An old fix', status: 'done', kind: 'fix',
             fixes_defects: [40, 43], fixes_open_defects: [] }),
  task(33, { title: 'Waits for the overlap fix, and has a title long enough to need the ellipsis at every width the page is read at',
             waits_on: { id: 31 }, blocked_by: [31] }),
  task(34, { title: 'Plain', kind: 'task' }),
  task(35, { title: 'Spec step', kind: 'spec', status: 'done' }),
]

const TITLES = [
  'Grid',
  'Defect rows open in place',
  'A slice title of middling length that still fits',
  'A slice whose title runs on and on, '.repeat(6).slice(0, 200),
]

const SLICES = {
  sliceable: true, refusal: null, problems: [], scenarios: { covered: 4, total: 4 },
  tail_missing: false,
  rows: TITLES.map((title, i) => ({
    n: `S${i + 1}`, title, depends_on: i ? [`S${i}`] : [],
    tests: [`test_slice_${i}_a`, `test_slice_${i}_b`, `test_slice_${i}_c`],
    base: 'f1806/integration', skill: 'goal-verified',
    task_id: i === 0 ? 31 : (i === 1 ? 33 : null),
    task_status: i === 0 ? 'running' : (i === 1 ? 'armed' : null),
    fixes_defects: i === 0 ? [41] : [], fixes_open_defects: i === 0 ? [41] : [],
  })),
}

const BRANCHES = {
  integration: { name: 'f1806/integration', source: 'convention', exists: true, merged_into_default: false },
  runs: [{ branch: 'f1806/s1', base: 'f1806/integration', task_id: 31, last_run_id: 901,
           worktree_path: '/tmp/wt', exists: true, merged_into_base: false }],
  error: null,
}

const REFS = {
  'task:31': { kind: 'task', key: '31', ref: 'task:31', title: 'Fix the overlap',
               status: 'running', url: '/tasks/31', exists: true },
  'task:32': { kind: 'task', key: '32', ref: 'task:32', title: 'An old fix',
               status: 'done', url: '/tasks/32', exists: true },
}

const page0 = items => ({ items, pagination: { total: items.length, page: 0, size: 200,
                                               has_next: false, has_prev: false } })

async function stub(page, { defects = DEFECTS } = {}) {
  await page.route(new RegExp(`/api/features/${FID}$`), route => route.fulfill({ json: ledger() }))
  await page.route(new RegExp(`/api/features/${FID}/slices$`), route => route.fulfill({ json: SLICES }))
  await page.route(new RegExp(`/api/features/${FID}/branches$`), route => route.fulfill({ json: BRANCHES }))
  await page.route('**/api/tasks/graph?**', route => route.fulfill({ json: {
    nodes: [], edges: [], lanes: [], chains: 0, lanes_hidden: 0, open_only: false,
    truncated: false, total_nodes: 0, node_cap: 150,
  } }))
  await page.route('**/api/tasks?**', route => route.fulfill({ json: { items: TASKS, next_cursor: null } }))
  await page.route(/\/api\/defects\?/, route => route.fulfill({ json: page0(defects) }))
  await page.route(/\/api\/defects\/\d+\/images\/\d+/, route => route.fulfill({
    contentType: 'image/png', body: Buffer.from(PNG_B64, 'base64'),
  }))
  await page.route(url => url.pathname === '/api/refs/resolve', (route) => {
    const asked = new URL(route.request().url()).searchParams.getAll('ref')
    return route.fulfill({ json: { refs: Object.fromEntries(asked.map(r => [r, REFS[r] ?? null])) } })
  })
}

const row = (page, id) => page.getByTestId(`defect-row-${id}`)
const head = (page, id) => page.getByTestId(`defect-head-${id}`)

const box = loc => loc.evaluate(el => {
  const r = el.getBoundingClientRect()
  return { top: r.top, left: r.left, height: r.height, width: r.width }
})

/** The data-cell names of a defect head, in DOM order. */
const cells = loc => loc.evaluate(el => [...el.querySelectorAll(':scope > [data-cell]')].map(c => c.dataset.cell))

async function openFeature(page, width = 1440) {
  await page.setViewportSize({ width, height: 900 })
  await stub(page)
  await page.goto(`/features/${FID}`)
  await expect(row(page, 41)).toBeVisible()
}

test('test_feature_defect_row_is_one_line', async ({ page }) => {
  await openFeature(page)
  const id = await box(page.getByTestId('defect-permalink-41'))
  const line = page.getByTestId('defect-line-41')
  await expect(line).toHaveText('The Task column drifts right [image 1]')
  expect(Math.abs((await box(line)).top - id.top)).toBeLessThanOrEqual(2)
  expect((await box(row(page, 41))).height).toBeLessThanOrEqual(40)
  await expect(row(page, 41).locator('[data-pl-part="facts"]')).toHaveCount(0)
  await expect(row(page, 41).locator('[data-pl-part="verbs"]')).toHaveCount(0)
  // The first non-empty line, not the blank lines a symptom opens with.
  await expect(page.getByTestId('defect-line-42')).toHaveText('No one is fixing this yet')
})

test('test_feature_defect_row_expands_in_place', async ({ page }) => {
  await openFeature(page)
  await page.getByTestId('defect-line-41').click()

  const body = page.getByTestId('defect-body-41')
  await expect(body).toBeVisible()
  await expect(row(page, 41)).toHaveAttribute('data-expanded', '')
  await expect(body.getByTestId('defect-symptom-41')).toContainText('third line')
  await expect(body.getByTestId('defect-symptom-image')).toBeVisible()
  await expect(body.locator('[data-pl-part="facts"]')).toContainText('frontend/src/x.vue')
  await expect(body.locator('[data-pl-part="verbs"]')).toBeVisible()
  // The head's fix column already says it: the facts do not repeat it.
  await expect(body.locator('[data-pl-part="facts"]')).not.toContainText('fix item')

  await body.getByTestId('defect-symptom-41').click()
  await expect(page.getByTestId('defect-edit-field-41')).toBeVisible()
  await expect(body).toBeVisible()
  await page.getByTestId('defect-edit-cancel-41').click()

  await page.getByTestId('defect-line-41').click()
  await expect(body).toHaveCount(0)
  expect((await box(row(page, 41))).height).toBeLessThanOrEqual(40)

  // Keyboard: Enter on the focused head opens it, Esc inside closes it.
  await head(page, 41).focus()
  await page.keyboard.press('Enter')
  await expect(body).toBeVisible()
  await body.locator('[data-pl-part="verbs"] button').first().focus()
  await page.keyboard.press('Escape')
  await expect(body).toHaveCount(0)
  await expect(head(page, 41)).toBeFocused()
})

test('test_defects_page_uses_the_one_line_head', async ({ page }) => {
  await openFeature(page)
  const onFeature = await cells(head(page, 41))
  await expect(row(page, 41)).not.toHaveAttribute('data-expanded', '')

  await page.goto('/defects')
  await expect(row(page, 41)).toBeVisible()
  await expect(page.getByTestId('defect-body-41')).toHaveCount(0)
  const onList = await cells(head(page, 41))

  const ORDER = ['select', 'id', 'symptom', 'status', 'fix', 'date', 'toggle']
  expect(onFeature).toEqual(ORDER)
  // The global list names the blamed feature; the feature page is headed by it.
  expect(onList.filter(c => c !== 'feature')).toEqual(ORDER)
  await expect(head(page, 41).locator('.ref-chip[data-ref="task:31"]')).toBeVisible()
  expect((await box(row(page, 41))).height).toBeLessThanOrEqual(48)
})

// One test, not one per width: the acceptance name must be a literal title
// for the spec's test index to find it.
test('test_slices_rows_align_in_columns', async ({ page }) => {
  for (const width of [1440, 1024]) {
    await test.step(`@${width}`, async () => {
      await openFeature(page, width)
      const rows = page.locator('[data-testid^="slice-row-"]')
      await expect(rows).toHaveCount(4)
      // clientHeight: the last row drops its bottom rule, which is not a taller row.
      const heights = await rows.evaluateAll(els => els.map(e => e.clientHeight))
      expect(Math.max(...heights) - Math.min(...heights)).toBeLessThanOrEqual(0.5)
      expect(Math.max(...heights)).toBeLessThanOrEqual(40)
      const taskX = await page.locator('[data-testid^="slice-row-"] [data-cell="task"]')
        .evaluateAll(els => els.map(e => e.getBoundingClientRect().left))
      expect(Math.max(...taskX) - Math.min(...taskX)).toBeLessThanOrEqual(1)

      const longest = page.getByTestId('slice-title-S4')
      expect(await longest.evaluate(el => el.scrollWidth > el.clientWidth)).toBe(true)
      expect(await longest.evaluate(el => getComputedStyle(el).textOverflow)).toBe('ellipsis')
      await expect(page.locator('[data-testid="slice-row-S4"] [data-cell="title"]'))
        .toHaveAttribute('title', TITLES[3])
      await expect(page.getByTestId('slice-tests-S1')).toHaveText('3 tests')
      await expect(page.getByTestId('slice-tests-S1'))
        .toHaveAttribute('title', 'test_slice_0_a\ntest_slice_0_b\ntest_slice_0_c')
    })
  }
})

test('test_slices_card_has_header_and_branch_footer', async ({ page }) => {
  await openFeature(page)
  const card = page.locator('#slices')
  const header = card.getByTestId('slices-header')
  const labels = await header.locator(':scope > *').evaluateAll(els => els.map(e => e.textContent.trim()))
  expect(labels).toEqual(['#', 'Slice', 'Tests', 'After', 'Base', 'Task'])
  // The header is the grid's first row, above every slice row.
  expect(await header.evaluate(el => el.parentElement.firstElementChild === el)).toBe(true)

  const foot = card.getByTestId('feature-branch')
  await expect(foot.getByTestId('feature-branch-name')).toHaveText('f1806/integration')
  await expect(foot.getByTestId('feature-branch-run-count')).toHaveText('· 1 run branch')
  expect(await foot.evaluate(el => el.parentElement.lastElementChild === el)).toBe(true)
  await foot.getByTestId('feature-branch-toggle').click()
  await expect(foot.getByTestId('feature-branch-run-901')).toBeVisible()

  // Slices is followed directly by Images: no block between them.
  const next = await card.evaluate(el => el.nextElementSibling?.id)
  expect(next).toBe('feature-images')
  await expect(page.locator('[data-testid="feature-branch"]')).toHaveCount(1)
})

test('test_tasks_rows_align_in_columns', async ({ page }) => {
  await openFeature(page)
  const rows = page.locator('[data-testid^="feature-task-row-"]')
  await expect(rows).toHaveCount(5)
  // clientHeight: the last row drops its bottom rule, which is not a taller row.
    const heights = await rows.evaluateAll(els => els.map(e => e.clientHeight))
  expect(Math.max(...heights) - Math.min(...heights)).toBeLessThanOrEqual(0.5)
  expect(Math.max(...heights)).toBeLessThanOrEqual(40)
  const titleX = await page.locator('[data-testid^="feature-task-row-"] [data-cell="title"]')
    .evaluateAll(els => els.map(e => e.getBoundingClientRect().left))
  expect(Math.max(...titleX) - Math.min(...titleX)).toBeLessThanOrEqual(1)
  await expect(page.getByTestId('feature-task-waits-33')).toBeVisible()
  await expect(page.getByTestId('feature-task-fixes-32-43')).toBeVisible()

  // Opening a task's sessions spans the row and moves no other row's columns.
  await page.getByTestId('feature-task-sessions-31').click()
  const list = page.getByTestId('feature-task-sessions-list-31')
  await expect(list).toBeVisible()
  const listBox = await box(list)
  const rowBox = await box(page.getByTestId('feature-task-row-31'))
  expect(listBox.width).toBeGreaterThan(rowBox.width * 0.8)
  const after = await page.locator('[data-testid^="feature-task-row-"] [data-cell="title"]')
    .evaluateAll(els => els.map(e => e.getBoundingClientRect().left))
  expect(Math.max(...after) - Math.min(...after)).toBeLessThanOrEqual(1)
})

test('test_feature_page_has_no_horizontal_overflow', async ({ page }) => {
  for (const width of [1920, 1440, 1024]) {
    await openFeature(page, width)
    await expect(page.locator('[data-testid^="slice-row-"]')).toHaveCount(4)
    await expect(page.locator('[data-testid^="feature-task-row-"]')).toHaveCount(5)
    const scroll = await page.locator('.content-scroll').evaluate(el => ({ sw: el.scrollWidth, cw: el.clientWidth }))
    expect(scroll.sw, `overflow at ${width}`).toBe(scroll.cw)
  }

  // A list narrower than 720px drops its minor columns, whatever the viewport.
  await openFeature(page, 900)
  await expect(page.locator('[data-testid^="slice-row-"]')).toHaveCount(4)
  const widths = await page.evaluate(() => ['slices-rows', 'feature-tasks-list', 'feature-defects-list']
    .map(t => document.querySelector(`[data-testid="${t}"]`).closest('.ledger-grid').clientWidth))
  for (const w of widths) expect(w).toBeLessThan(720)
  for (const sel of ['[data-cell="after"]', '[data-cell="base"]', '[data-cell="runs"]', '[data-cell="date"]']) {
    const shown = await page.locator(sel).evaluateAll(els => els.filter(e => e.offsetParent !== null).length)
    expect(shown, sel).toBe(0)
  }
  const scroll = await page.locator('.content-scroll').evaluate(el => ({ sw: el.scrollWidth, cw: el.clientWidth }))
  expect(scroll.sw).toBe(scroll.cw)
})

test('test_defect_row_shows_fix_task_chip_with_status', async ({ page }) => {
  await openFeature(page)
  const chip = head(page, 41).locator('.ref-chip[data-ref="task:31"]')
  await expect(chip).toBeVisible()
  await expect(chip).toContainText('Fix the overlap')
  await expect(chip).toHaveAttribute('data-status', 'running')
  await expect(chip.locator('.ref-chip-dot')).toHaveAttribute('aria-label', 'running')
  await expect(page.getByTestId('defect-fix-41')).toHaveAttribute('data-tone', 'warn')
  await expect(page.getByTestId('defect-body-41')).toHaveCount(0)
  // A closed defect's fix is a neutral chip.
  await expect(page.getByTestId('defect-fix-40')).toHaveAttribute('data-tone', 'neutral')
})

test('test_open_defect_without_fix_task_says_so', async ({ page }) => {
  await openFeature(page)
  await expect(page.getByTestId('defect-fix-42')).toHaveText('no fix task')
  await expect(page.getByTestId('defect-fix-42').locator('.ref-chip')).toHaveCount(0)
})

test('test_fix_task_row_is_accented_in_tasks_list', async ({ page }) => {
  await openFeature(page)
  const accented = page.getByTestId('feature-task-row-31')
  const plain = page.getByTestId('feature-task-row-32')
  await expect(accented).toHaveAttribute('data-fix-open', '')
  await expect(plain).not.toHaveAttribute('data-fix-open', '')
  const shadow = loc => loc.evaluate(el => getComputedStyle(el).boxShadow)
  expect(await shadow(accented)).toMatch(/inset/)
  expect(await shadow(accented)).toMatch(/3px 0px 0px/)
  expect(await shadow(plain)).toBe('none')

  await expect(page.getByTestId('feature-task-fixes-31-41')).toHaveAttribute('data-tone', 'warn')
  await expect(page.getByTestId('feature-task-fixes-32-40')).not.toHaveAttribute('data-tone', 'warn')
  const bg = loc => loc.evaluate(el => getComputedStyle(el).backgroundColor)
  expect(await bg(page.getByTestId('feature-task-fixes-31-41')))
    .not.toBe(await bg(page.getByTestId('feature-task-fixes-32-40')))
  // The slice whose task fixes an open defect carries the same chip.
  await expect(page.getByTestId('slice-fixes-S1-41')).toHaveAttribute('data-tone', 'warn')
})
