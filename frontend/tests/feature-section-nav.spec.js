/**
 * Feature #125 R2: the feature page's section index, the inline Project
 * picker, and the images anchor.
 *
 * Every read the page fires is stubbed, so the counts asserted here are the
 * ones this file seeded — a count that drifted from its section would show as
 * a mismatch against these fixtures, not as an empty database.
 */
import { test, expect } from './auth-fixture.js'

const FID = 242
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
  'base64')

function sessionRow(i) {
  return {
    trace_id: `trace${String(i).padStart(12, '0')}`, short_trace: `trace${i}`,
    date: '2026-09-01', title: `session ${i}`, cost_usd: 1,
    provenance: 'declared', round: 1, is_primary: i === 0,
  }
}

function ledger(overrides = {}) {
  return {
    id: FID, title: 'Section index', origin: 'native',
    body: '# Spec\n\nThe page needs a table of contents.',
    external_ref: null, external_url: null, sync_error: null,
    project_id: 1, project_name: 'regin',
    stage: {
      stage: 'building', blurb: 'in progress', rounds: 1,
      claimed_at: null, open_defects: 1, escapes: 0,
    },
    synced_at: null, stage_live: true, verbs: [], ship_check: null,
    next_step: null, slice_tasks: 3,
    images: [{ idx: 1 }, { idx: 2 }],
    cost: { usd: 9, sessions: 9, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 60_000, gaps: {} },
    tokens: {},
    sessions: { rows: Array.from({ length: 9 }, (_, i) => sessionRow(i)), gaps: {} },
    defects: { open: 1, total: 5, escapes: 0, gaps: {} },
    coverage: { declared_sessions: 9, all_sessions: 100, declared_usd: 9, all_usd: 100 },
    ...overrides,
  }
}

function task(id, status) {
  return {
    id, title: `step ${id}`, status, kind: 'task', feature_id: FID,
    created_at: '2026-09-01 00:00:00', updated_at: '2026-09-01 00:00:00',
  }
}

const TASKS = [
  task(1, 'done'), task(2, 'done'), task(3, 'done'),
  task(4, 'idea'), task(5, 'idea'), task(6, 'running'), task(7, 'failed'),
]

function defect(id, status) {
  return {
    id, symptom: `defect ${id}`, status, feature_id: FID,
    opened_at: '2026-09-01 00:00:00', updated_at: '2026-09-01 00:00:00',
  }
}

async function stub(page, { body = ledger(), patch = null } = {}) {
  await page.route(new RegExp(`/api/features/${FID}$`), async (route) => {
    if (route.request().method() === 'PATCH' && patch) return patch(route)
    return route.fulfill({ json: body })
  })
  await page.route(new RegExp(`/api/features/${FID}/slices$`), route => route.fulfill({
    json: { sliceable: false, refusal: null, tail_missing: false, rows: [] },
  }))
  await page.route(new RegExp(`/api/features/${FID}/branches$`), route => route.fulfill({
    json: { integration: { name: '', source: null, exists: false, merged_into_default: null },
            runs: [] },
  }))
  await page.route(new RegExp(`/api/features/${FID}/images/\\d+$`), route => route.fulfill({
    body: PNG, contentType: 'image/png',
  }))
  await page.route('**/api/tasks?**', route => route.fulfill({
    json: { items: TASKS, next_cursor: null },
  }))
  await page.route('**/api/defects?feature_id=*', route => route.fulfill({
    json: {
      items: [defect(1, 'open'), ...[2, 3, 4, 5].map(i => defect(i, 'fixed'))],
      scope: 'feature', pagination: { total: 5 },
    },
  }))
  await page.route('**/api/repos', route => route.fulfill({
    json: { repos: [{ id: 1, name: 'regin' }, { id: 2, name: 'claude-term' }] },
  }))
}

async function asViewer(page) {
  await page.addInitScript(() => {
    const raw = localStorage.getItem('regin_auth_user')
    if (!raw) return
    const user = JSON.parse(raw)
    user.role = 'viewer'
    localStorage.setItem('regin_auth_user', JSON.stringify(user))
  })
}

/** Top of `sel` minus the bottom of the sticky index, in the pane's frame. */
function gapUnderNav(page, sel) {
  return page.evaluate((s) => {
    const nav = document.querySelector('[data-testid=feature-section-nav]')
    return Math.round(document.querySelector(s).getBoundingClientRect().top
      - nav.getBoundingClientRect().bottom)
  }, sel)
}

test('test_ledger_section_index_counts_and_jump', async ({ page }) => {
  await stub(page)
  await page.goto('/overview')
  await page.goto(`/features/${FID}`)

  const nav = page.getByTestId('feature-section-nav')
  await expect(page.getByTestId('section-nav-tasks')).toContainText('3/7')
  await expect(page.getByTestId('section-nav-images')).toContainText('2')
  const labels = (await nav.locator('.fsn-link').allInnerTexts())
    .map(t => t.replace(/\s+/g, ' ').trim())
  expect(labels).toEqual([
    'Spec', 'Related', 'Slices 3', 'Images 2', 'Tasks 3/7', 'Defects 1/5', 'Sessions 9'])

  // Directly under the stage line, before the figures.
  const order = await page.evaluate(() => {
    const line = document.querySelector('.stage-line')
    const idx = document.querySelector('[data-testid=feature-section-nav]')
    return line.nextElementSibling === idx
  })
  expect(order).toBe(true)

  await page.getByTestId('section-nav-tasks').click()
  await expect(page).toHaveURL(new RegExp(`/features/${FID}#feature-tasks$`))
  await expect.poll(() => gapUnderNav(page, '#feature-tasks')).toBeGreaterThanOrEqual(0)
  await expect.poll(() => gapUnderNav(page, '#feature-tasks')).toBeLessThan(24)
  // The index stays put while the page scrolls under it.
  await expect(nav).toBeInViewport()
  const navTop = await nav.evaluate(el => Math.round(el.getBoundingClientRect().top
    - el.closest('.content-scroll').getBoundingClientRect().top))
  expect(navTop).toBe(0)

  await page.getByTestId('section-nav-defects').click()
  await expect(page).toHaveURL(/#feature-defects$/)

  // Replace, not push: Back leaves the page rather than walking the jumps.
  await page.goBack()
  await expect(page).toHaveURL(/\/overview/)
})

test('test_ledger_section_index_current_on_scroll', async ({ page }) => {
  await stub(page)
  await page.goto(`/features/${FID}`)
  await expect(page.getByTestId('section-nav-tasks')).toContainText('3/7')

  await expect(page.getByTestId('section-nav-spec')).toHaveAttribute('aria-current', 'location')

  // Scrolled by the pane itself, not by the index: the observer has to notice.
  await page.evaluate(() => document.getElementById('feature-tasks')
    .scrollIntoView({ block: 'start' }))
  await expect(page.getByTestId('section-nav-tasks')).toHaveAttribute('aria-current', 'location')
  await expect(page.locator('[data-testid=feature-section-nav] [aria-current]')).toHaveCount(1)

  await page.evaluate(() => document.getElementById('slices')
    .scrollIntoView({ block: 'start' }))
  await expect(page.getByTestId('section-nav-slices')).toHaveAttribute('aria-current', 'location')
  await expect(page.getByTestId('section-nav-tasks')).not.toHaveAttribute('aria-current', /.*/)
})

test('test_ledger_section_index_current_at_page_foot', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await stub(page, { body: ledger({ sessions: { rows: [sessionRow(0)], gaps: {} } }) })
  await page.goto(`/features/${FID}`)
  await expect(page.getByTestId('section-nav-tasks')).toContainText('3/7')

  await page.getByTestId('section-nav-sessions').click()
  await expect(page).toHaveURL(/#feature-sessions$/)
  const pane = page.locator('.content-scroll')
  await expect.poll(() => pane.evaluate(el => el.scrollHeight - el.clientHeight - el.scrollTop))
    .toBeLessThan(2)
  await expect(page.getByTestId('section-nav-sessions')).toHaveAttribute('aria-current', 'location')

  // Clicking a mid-page entry that also cannot reach the line holds too.
  await page.getByTestId('section-nav-defects').click()
  await expect(page.getByTestId('section-nav-defects')).toHaveAttribute('aria-current', 'location')

  // The reader scrolls up: the pin lets go and the line decides again.
  await pane.hover()
  await page.mouse.wheel(0, -100000)
  await expect.poll(() => pane.evaluate(el => el.scrollTop)).toBe(0)
  await expect(page.getByTestId('section-nav-spec')).toHaveAttribute('aria-current', 'location')

  // Scrolled to the foot by hand, the last entry is the one marked.
  await page.mouse.wheel(0, 100000)
  await expect(page.getByTestId('section-nav-sessions')).toHaveAttribute('aria-current', 'location')
})

// A scroll with no wheel, touch or key behind it (a scrollbar drag) still
// lets go of a pinned entry once its section has left the screen.
test('test_ledger_section_index_pin_released_by_any_scroll', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await stub(page, { body: ledger({ sessions: { rows: [sessionRow(0)], gaps: {} } }) })
  await page.goto(`/features/${FID}`)
  await expect(page.getByTestId('section-nav-tasks')).toContainText('3/7')

  await page.getByTestId('section-nav-defects').click()
  const pane = page.locator('.content-scroll')
  await expect.poll(() => pane.evaluate(el => el.scrollHeight - el.clientHeight - el.scrollTop))
    .toBeLessThan(2)
  await expect(page.getByTestId('section-nav-defects')).toHaveAttribute('aria-current', 'location')

  await pane.evaluate((el) => { el.scrollTop = 0 })
  await expect(page.getByTestId('section-nav-spec')).toHaveAttribute('aria-current', 'location')
})

test('test_ledger_section_index_phone_no_overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await stub(page)
  await page.goto(`/features/${FID}`)
  await expect(page.getByTestId('section-nav-tasks')).toContainText('3/7')

  const m = await page.evaluate(() => {
    const pane = document.querySelector('.content-scroll')
    const list = document.querySelector('[data-testid=feature-section-nav] .fsn-list')
    const links = [...list.querySelectorAll('.fsn-link')]
    const tops = new Set(links.map(a => Math.round(a.getBoundingClientRect().top)))
    return {
      paneOverflow: pane.scrollWidth - pane.clientWidth,
      docOverflow: document.documentElement.scrollWidth - window.innerWidth,
      rows: tops.size,
      scrolls: list.scrollWidth > list.clientWidth,
      overflowX: getComputedStyle(list).overflowX,
    }
  })
  expect(m.paneOverflow).toBeLessThanOrEqual(0)
  expect(m.docOverflow).toBeLessThanOrEqual(0)
  expect(m.rows).toBe(1)
  expect(m.overflowX).toBe('auto')

  // The last entry is reachable by scrolling the strip, and still jumps.
  await page.getByTestId('section-nav-sessions').click()
  await expect(page).toHaveURL(/#feature-sessions$/)
})

test('test_ledger_project_inline', async ({ page }) => {
  let sent = null
  await stub(page, {
    patch: async (route) => {
      sent = route.request().postDataJSON()
      return route.fulfill({ json: ledger({ project_id: 2, project_name: 'claude-term' }) })
    },
  })
  await page.goto(`/features/${FID}`)

  await expect(page.getByTestId('feature-project')).toHaveCount(0)
  const inline = page.locator('.stage-line').getByTestId('stage-project')
  await expect(inline.getByTestId('feature-project-select')).toContainText('regin')

  await inline.getByTestId('feature-project-select').click()
  await page.getByRole('option', { name: 'claude-term' }).click()
  await expect(inline.getByTestId('feature-project-saved')).toHaveText('Saved')
  expect(sent).toEqual({ project_id: 2 })
  await expect(inline.getByTestId('feature-project-select')).toContainText('claude-term')
  await expect(page.locator('.stage-line [role=alert]')).toHaveCount(0)
})

test('test_ledger_project_inline refusal stays beside the control', async ({ page }) => {
  await stub(page, {
    patch: route => route.fulfill({ status: 409, json: { error: 'project 2 is archived' } }),
  })
  await page.goto(`/features/${FID}`)
  const inline = page.locator('.stage-line').getByTestId('stage-project')
  await inline.getByTestId('feature-project-select').click()
  await page.getByRole('option', { name: 'claude-term' }).click()
  await expect(inline.getByTestId('feature-project-error')).toBeVisible()
  await expect(inline.getByTestId('feature-project-saved')).toHaveCount(0)
  // The page survives the refusal.
  await expect(page.getByTestId('feature-section-nav')).toBeVisible()
})

test('test_ledger_project_inline viewer reads the name', async ({ page }) => {
  await asViewer(page)
  await stub(page)
  await page.goto(`/features/${FID}`)
  const inline = page.locator('.stage-line').getByTestId('stage-project')
  await expect(inline.getByTestId('feature-project-static')).toHaveText('regin')
  await expect(inline.getByTestId('feature-project-select')).toHaveCount(0)
})

test('test_ledger_stage_counts_link_to_defects', async ({ page }) => {
  await stub(page, { body: ledger({ stage: {
    stage: 'shipped', blurb: '', rounds: 2, claimed_at: null, open_defects: 1, escapes: 2,
  } }) })
  await page.goto(`/overview`)
  await page.goto(`/features/${FID}`)
  await page.getByTestId('stage-escaped-link').click()
  await expect(page).toHaveURL(/#feature-defects$/)
  await expect(page.locator('#feature-defects')).toBeInViewport()
  await expect(page.getByTestId('stage-open-link')).toHaveAttribute('href', /#feature-defects$/)
  await page.goBack()
  await expect(page).toHaveURL(/\/overview/)
})

test('test_ledger_stage_counts_zero_are_text', async ({ page }) => {
  await stub(page, { body: ledger({ stage: {
    stage: 'building', blurb: '', rounds: 1, claimed_at: null, open_defects: 0, escapes: 0,
  } }) })
  await page.goto(`/features/${FID}`)
  await expect(page.locator('.stage-counts')).toContainText('0 open')
  await expect(page.getByTestId('stage-open-link')).toHaveCount(0)
  await expect(page.getByTestId('stage-escaped-link')).toHaveCount(0)
})

test('test_ledger_images_anchor_arrival', async ({ page }) => {
  await stub(page)
  await page.goto(`/features/${FID}#feature-images`)

  const card = page.locator('#feature-images')
  await expect(card).toHaveAttribute('data-testid', 'feature-images')
  await expect(card).toBeInViewport()
  // Under the strip, not behind it; the cards above may still settle their
  // own async content after the arrival reveal, so the bound is loose.
  await expect.poll(() => gapUnderNav(page, '#feature-images')).toBeGreaterThanOrEqual(0)
  await expect.poll(() => gapUnderNav(page, '#feature-images')).toBeLessThan(64)
})
