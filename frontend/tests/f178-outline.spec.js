/**
 * F178 S4 — a long body's outline: a rail in the focus layout's left margin
 * on a wide screen, an Outline button in the head (inline layout) and in the
 * spec card's head (read view) once there are four headings, and nothing at
 * all on a task's body.
 *
 * Stubbed like `f176-layout.spec.js`: the ledger is a fixture.
 */
import { test, expect } from './auth-fixture.js'

const FEATURE = 1781

function ledger(body) {
  return {
    id: FEATURE, title: 'Outline spec', origin: 'native',
    external_ref: null, external_url: null, sync_error: null, body,
    stage: {
      stage: 'building', blurb: 'being built', rounds: 1,
      claimed_at: null, open_defects: 0, escapes: 0,
    },
    synced_at: null, stage_live: true, images: [],
    cost: { usd: 1, sessions: 1, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 1000, gaps: {} },
    tokens: { input: 1, output: 1 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: { declared_sessions: 1, all_sessions: 2, declared_usd: 1, all_usd: 2 },
  }
}

// `count` sections, each a `##` heading over 12 paragraphs (one `###` in the
// middle of the first), so a heading can scroll to the top of the pane.
function spec(count) {
  const parts = []
  for (let s = 0; s < count; s++) {
    parts.push(`## Part ${s}`)
    for (let i = 0; i < 12; i++) parts.push(`para ${s}.${i}`)
  }
  return parts.join('\n\n')
}

async function stubFeature(page, body) {
  let current = body
  await page.route(new RegExp(`/api/features/${FEATURE}$`), (route) => {
    if (route.request().method() === 'PATCH') current = route.request().postDataJSON().body
    return route.fulfill({ json: ledger(current) })
  })
  await page.route(new RegExp(`/api/features/${FEATURE}/(tasks|defects)`), (route) =>
    route.fulfill({ json: { items: [] } }))
}

const editor = (page) => page.getByTestId('markdown-editor')
const face = (page) => page.getByTestId('feature-spec-rich')
const rail = (page) => page.getByTestId('outline-rail')
const toggle = (page) => page.getByTestId('outline-toggle')

async function openEditor(page) {
  await page.goto(`/features/${FEATURE}`)
  await page.getByTestId('feature-spec-edit').click()
  await expect(face(page)).toBeVisible()
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => window.localStorage.removeItem('regin_editor_mode'))
})

// The rail is a gutter cut from the full-width focus column, offered while
// the column keeps 640px beside it: with the sidebar open a 1440px window
// has room, a 1024px one does not and the head's button stands in for it.
test('test_outline_rail_in_focus', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await stubFeature(page, spec(8))
  await openEditor(page)
  await page.getByTestId('editor-expand').click()
  await expect(editor(page)).toHaveAttribute('data-layout', 'focus')

  const items = rail(page).getByTestId('outline-item')
  await expect(items).toHaveCount(8)
  await expect(items.nth(0)).toHaveText('Part 0')

  // Left of the text, not under it, inside the pane; the text runs on to the
  // column's right edge rather than stopping short of it.
  const geo = await page.evaluate(() => {
    const r = (el) => el.getBoundingClientRect()
    const ed = document.querySelector('[data-testid="markdown-editor"]')
    const text = document.querySelector('[data-testid="feature-spec-rich"]')
    const nav = document.querySelector('[data-testid="outline-rail"]')
    const pane = document.querySelector('.content-scroll')
    return {
      edRight: r(ed).right, textLeft: r(text).left, textRight: r(text).right,
      railLeft: r(nav).left, railRight: r(nav).right,
      paneLeft: r(pane).left + parseFloat(getComputedStyle(pane).paddingLeft),
    }
  })
  expect(geo.railRight).toBeLessThanOrEqual(geo.textLeft)
  expect(geo.railLeft).toBeGreaterThanOrEqual(geo.paneLeft)
  expect(Math.abs(geo.textRight - geo.edRight)).toBeLessThanOrEqual(1)

  await items.nth(5).click()
  const heading = face(page).locator('h2').nth(5)
  await expect(heading).toHaveText('Part 5')
  const headBottom = await page.getByTestId('editor-head').evaluate((el) => el.getBoundingClientRect().bottom)
  const top = await heading.evaluate((el) => el.getBoundingClientRect().top)
  expect(Math.abs(top - headBottom)).toBeLessThanOrEqual(2)
  await expect(items.nth(5)).toHaveAttribute('aria-current', 'true')
  await expect(rail(page).locator('[aria-current="true"]')).toHaveCount(1)
  // The caret is at the start of that heading.
  const caret = await page.evaluate(() => {
    const sel = window.getSelection()
    const block = sel.anchorNode?.nodeType === 1 ? sel.anchorNode : sel.anchorNode?.parentElement
    return { tag: block?.closest('h2')?.textContent, offset: sel.anchorOffset }
  })
  expect(caret).toEqual({ tag: 'Part 5', offset: 0 })

  // Turning a paragraph into a heading adds an entry: the list follows the
  // document. Up from the heading's start is the start of the paragraph above.
  await page.keyboard.press('ArrowUp')
  await page.keyboard.type('## ')
  await expect(items).toHaveCount(9)
  await expect(items.nth(5)).toHaveText('para 4.11')

  await page.getByTestId('editor-mode-source').click()
  await expect(rail(page)).toHaveCount(0)
  await page.getByTestId('editor-mode-rich').click()
  await expect(rail(page)).toBeVisible()
  await page.getByTestId('editor-collapse').click()
  await expect(rail(page)).toHaveCount(0)
})

test('test_outline_rail_in_focus — no headings says so', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await stubFeature(page, 'just a paragraph')
  await openEditor(page)
  await page.getByTestId('editor-expand').click()
  await expect(rail(page)).toContainText('No headings')
  await expect(rail(page).getByTestId('outline-item')).toHaveCount(0)
})

test('test_outline_rail_in_focus — a column too narrow for it gets the button instead', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 900 })
  await stubFeature(page, spec(8))
  await openEditor(page)
  await page.getByTestId('editor-expand').click()
  await expect(editor(page)).toHaveAttribute('data-layout', 'focus')
  await expect(rail(page)).toHaveCount(0)
  await expect(toggle(page)).toBeVisible()
})

test('test_outline_dropdown', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await stubFeature(page, spec(8))

  // Read view: the spec card's head carries the button.
  await page.goto(`/features/${FEATURE}`)
  const card = page.getByTestId('feature-spec')
  await expect(card.getByTestId('outline-toggle')).toBeVisible()
  await card.getByTestId('outline-toggle').click()
  const list = page.getByTestId('outline-list')
  await expect(list.getByTestId('outline-item')).toHaveCount(8)
  await list.getByTestId('outline-item').nth(4).click()
  await expect(list).toHaveCount(0)
  const read = page.getByTestId('feature-spec-body').locator('h2').nth(4)
  await expect(read).toHaveText('Part 4')
  const landed = await read.evaluate((el) => {
    const nav = document.querySelector('[data-testid="feature-section-nav"]').getBoundingClientRect()
    const r = el.getBoundingClientRect()
    return { top: r.top, navBottom: nav.bottom, pane: el.closest('.content-scroll').getBoundingClientRect().bottom }
  })
  expect(landed.top).toBeGreaterThanOrEqual(landed.navBottom - 1)
  expect(landed.top).toBeLessThan(landed.pane)

  // Inline editing: the editor's head carries it.
  await page.getByTestId('feature-spec-edit').click()
  await expect(face(page)).toBeVisible()
  await expect(editor(page)).toHaveAttribute('data-layout', 'inline')
  await expect(rail(page)).toHaveCount(0)
  const headToggle = page.getByTestId('editor-head').getByTestId('outline-toggle')
  await headToggle.click()
  await expect(list.getByTestId('outline-item')).toHaveCount(8)
  await list.getByTestId('outline-item').nth(6).click()
  await expect(list).toHaveCount(0)
  const heading = face(page).locator('h2').nth(6)
  const headBottom = await page.getByTestId('editor-head').evaluate((el) => el.getBoundingClientRect().bottom)
  const top = await heading.evaluate((el) => el.getBoundingClientRect().top)
  expect(Math.abs(top - headBottom)).toBeLessThanOrEqual(2)
  // Focus went to the heading, not back to the button.
  await page.keyboard.type('X')
  await expect(heading).toHaveText('XPart 6')
})

test('test_outline_dropdown — two headings show no button', async ({ page }) => {
  await stubFeature(page, spec(2))
  await page.goto(`/features/${FEATURE}`)
  await expect(page.getByTestId('feature-spec-body').locator('h2')).toHaveCount(2)
  await expect(page.getByTestId('outline-toggle')).toHaveCount(0)
  await page.getByTestId('feature-spec-edit').click()
  await expect(face(page)).toBeVisible()
  await expect(page.getByTestId('outline-toggle')).toHaveCount(0)
})

test('test_outline_dropdown — a spec saved empty leaves no button behind', async ({ page }) => {
  await stubFeature(page, spec(8))
  await page.goto(`/features/${FEATURE}`)
  await expect(page.getByTestId('feature-spec').getByTestId('outline-toggle')).toBeVisible()
  await page.getByTestId('feature-spec-edit').click()
  await page.getByTestId('editor-mode-source').click()
  await page.getByTestId('feature-spec-input').fill('')
  await page.getByTestId('feature-spec-save').click()
  await expect(page.getByTestId('feature-spec-empty')).toBeVisible()
  await expect(page.getByTestId('outline-toggle')).toHaveCount(0)
})

test('test_task_body_has_no_outline', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const body = Array.from({ length: 6 }, (_, i) => `## Heading ${i}\n\ntext ${i}`).join('\n\n')
  const task = {
    id: 1782, title: 'Task with headings', body, status: 'idea',
    source: 'web', tags: [], links: [], params: {}, autorun: false, project_id: null,
    priority: 'none', priority_rank: 4, parent_id: null, assignee: null, assignee_name: null,
    rounds: 1, created_at: '2026-10-25', updated_at: '2026-10-25', external_id: null,
    created_by: null, images: [],
  }
  await page.route(/\/api\/tasks\/1782$/, (route) =>
    route.fulfill({ json: { task, grain: { children: 0, runs: 0 } } }))
  await page.route(/\/api\/tasks\/1782\/comments$/, (route) => route.fulfill({ json: { threads: [] } }))
  await page.route(/\/api\/tasks\/1782\/links$/, (route) => route.fulfill({ json: { links: [] } }))
  await page.goto('/tasks/1782')
  await expect(page.getByTestId('task-body-view').locator('h2')).toHaveCount(6)
  await expect(page.getByTestId('outline-toggle')).toHaveCount(0)

  await page.getByTestId('task-body-edit').click()
  await expect(page.getByTestId('task-body-rich')).toBeVisible()
  await expect(page.getByTestId('outline-toggle')).toHaveCount(0)
  await page.getByTestId('editor-expand').click()
  await expect(editor(page).filter({ has: page.getByTestId('task-body-rich') }))
    .toHaveAttribute('data-layout', 'focus')
  await expect(page.getByTestId('outline-rail')).toHaveCount(0)
  await expect(page.getByTestId('outline-toggle')).toHaveCount(0)
})
