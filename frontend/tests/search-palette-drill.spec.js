/**
 * The palette's list, its overflow, and its Drill (feature #146, R1/R2/R7/R8).
 *
 * `/api/search` is answered from a fixture here, because what these tests pin
 * is arithmetic over the server's numbers — a group's `total`, the `offset`
 * a Drill asks for, which kinds came back empty — and the scratch DB cannot be
 * made to hold exactly 19 matching sessions. The address miss runs against
 * the real resolver: the note's wording is the server's.
 */
import { test, expect } from './auth-fixture.js'

const PANEL = '[role="dialog"][aria-label="Full-text search"]'
const INPUT = `${PANEL} .query-input`
const OPTION = `${PANEL} [role="option"]`
const ACTIVE = `${PANEL} [role="option"][aria-selected="true"]`
const group = label => `${PANEL} [role="group"][aria-label="${label}"]`
const facet = kind => `${PANEL} .facet[data-kind="${kind}"]`

const TOTALS = { feature: 1, task: 2, defect: 0, session: 19, pattern: 2, file: 1 }

function doc(kind, i) {
  const id = kind === 'session' ? `sess-${String(i).padStart(4, '0')}-fixture` : String(100 + i)
  const ref = { feature: 'F', task: 'T', defect: 'D' }[kind]
  const base = {
    kind, id, title: `palette ${kind} ${i}`, sub: '', tag: '', crumb: '',
    body: `palette ${kind} ${i}`, mono: false, bodyHits: 1,
  }
  if (kind === 'pattern' || kind === 'file') return { ...base, title: `palette/${kind}-${i}.md` }
  return {
    ...base,
    href: kind === 'session' ? `/trace/sessions/${id}` : `/${kind}s/${id}`,
    row: {
      kind,
      ref: ref ? `${ref}${id}` : `#${id.slice(0, 8)}`,
      title: `palette ${kind} ${i}`,
      status: { word: 'ended', bucket: 'done' },
      owners: kind === 'session' ? [{ kind: 'feature', id: 146, ref: 'F146', title: '' }] : [],
      context: `prompt about palette ${i}`,
    },
  }
}

// `hold(kind)` keeps that kind's request open until the returned release is
// called; `fail(kind, offset)` answers that page with a 500 the first time.
async function mockSearch(page, { hold = {}, fail = {} } = {}) {
  const calls = []
  await page.route('**/api/search?**', async (route) => {
    const url = new URL(route.request().url())
    const kind = url.searchParams.get('kinds')
    const offset = Number(url.searchParams.get('offset') || 0)
    const limit = Number(url.searchParams.get('limit') || 6)
    calls.push({ kind, offset, limit })
    if (hold[kind]) await hold[kind]
    const failKey = `${kind}@${offset}`
    if (fail[failKey]) {
      delete fail[failKey]
      await route.fulfill({ status: 500, json: { error: 'boom' } })
      return
    }
    const total = TOTALS[kind] ?? 0
    const items = []
    for (let i = offset; i < Math.min(total, offset + limit); i += 1) items.push(doc(kind, i))
    await route.fulfill({ json: {
      q: url.searchParams.get('q'), groups: [{ kind, total, offset, items }],
      failed: [], truncated: false,
    } })
  })
  return calls
}

async function openOn(page, q) {
  await page.goto('/inbox')
  await page.locator('body').click()
  await page.keyboard.press('Meta+k')
  await page.waitForSelector(PANEL)
  await page.locator(INPUT).fill(q)
}

async function openSessionDrill(page) {
  await openOn(page, 'palette')
  const showAll = page.locator(`${group('Sessions')} [data-entry="more"]`)
  await expect(showAll).toHaveText(/Show all 19 sessions →/)
  await showAll.click()
  await expect(page.locator(`${PANEL} .drill-back`)).toBeVisible()
}

const activeId = page => page.locator(ACTIVE).getAttribute('id')

test.describe('search palette — overflow and Drill', () => {
  test('test_show_all_reaches_every_session', async ({ page }) => {
    await mockSearch(page)
    await openOn(page, 'palette')
    const sessions = page.locator(`${group('Sessions')} [role="option"]:not([data-entry])`)
    await expect(sessions).toHaveCount(4)

    // ↓ alone reaches the "Show all" row; ↵ on it opens the Drill.
    for (let n = 0; n < 40; n += 1) {
      if ((await page.locator(ACTIVE).getAttribute('data-entry')) === 'more'
        && (await page.locator(ACTIVE).textContent()).includes('sessions')) break
      await page.keyboard.press('ArrowDown')
    }
    await expect(page.locator(ACTIVE)).toHaveText(/Show all 19 sessions →/)
    await page.keyboard.press('Enter')
    await expect(page.locator(`${PANEL} .drill-label`)).toHaveText('Sessions')
    await expect(page.locator(`${PANEL} .query-count`)).toHaveText('19 sessions')

    // Every one of the 19 is reachable by ↓ — the load row pulls the next
    // page in and hands the cursor to its first row.
    // The first page settles before anything is picked; then each ↓ must
    // move the cursor off the row it was on.
    await expect(page.locator(ACTIVE)).toHaveCount(1)
    const seen = new Set()
    for (let n = 0; n < 30 && seen.size < 19; n += 1) {
      const id = await activeId(page)
      if (id.startsWith("search-row-session-")) seen.add(id)
      await page.keyboard.press('ArrowDown')
      await expect(page.locator(ACTIVE)).not.toHaveAttribute('id', id)
      await expect(page.locator(ACTIVE)).not.toHaveAttribute('data-entry', 'load')
    }
    expect(seen.size).toBe(19)
    await expect(page.locator(`${PANEL} .drill-end`)).toHaveText('end of list · all 19 shown')

    // esc steps out of the Drill, not out of the palette.
    await page.keyboard.press('Escape')
    await expect(page.locator(`${PANEL} .drill-back`)).toHaveCount(0)
    await expect(page.locator(group('Sessions'))).toBeVisible()
  })

  test('test_drill_pages_by_twelve', async ({ page }) => {
    const calls = await mockSearch(page)
    await openSessionDrill(page)
    const rows = page.locator(`${PANEL} [role="option"]:not([data-entry])`)
    await expect(rows).toHaveCount(12)
    const load = page.locator(`${PANEL} [data-entry="load"]`)
    await expect(load).toHaveText('Load 7 more · 7 left')
    await load.click()
    await expect(rows).toHaveCount(19)
    await expect(load).toHaveCount(0)
    const pages = calls.filter(c => c.kind === 'session' && c.limit === 12)
    expect(pages.map(c => c.offset)).toEqual([0, 12])
  })

  test('test_drill_page_failure_retries', async ({ page }) => {
    await mockSearch(page, { fail: { 'session@12': true } })
    await openSessionDrill(page)
    const rows = page.locator(`${PANEL} [role="option"]:not([data-entry])`)
    await expect(rows).toHaveCount(12)
    await page.locator(`${PANEL} [data-entry="load"]`).click()
    const retry = page.locator(`${PANEL} [data-entry="retry"]`)
    await expect(retry).toHaveText("Couldn't load the next page · retry")
    // The page that did load stays; only the failed one is asked again.
    await expect(rows).toHaveCount(12)
    await retry.click()
    await expect(rows).toHaveCount(19)
    await expect(retry).toHaveCount(0)
    await expect(page.locator(`${PANEL} .drill-end`)).toHaveText('end of list · all 19 shown')
  })

  test('test_facet_count_equals_total', async ({ page }) => {
    let release
    const held = new Promise((r) => { release = r })
    await mockSearch(page, { hold: { feature: held } })
    await openOn(page, 'palette')

    // While Features is still out: its chip says so, All is a floor, and no
    // row has been preselected for the late group to push down.
    await expect(page.locator(`${facet('feature')} .facet-count`)).toHaveText('…')
    await expect(page.locator(`${facet('session')} .facet-count`)).toHaveText('19')
    await expect(page.locator(`${facet('all')} .facet-count`)).toHaveText('24+')
    await expect(page.locator(`${PANEL} .query-count`)).toContainText('1 kind loading')
    await expect(page.locator(ACTIVE)).toHaveCount(0)

    release()
    const expected = { ...TOTALS, all: 25 }
    for (const [kind, n] of Object.entries(expected)) {
      await expect(page.locator(`${facet(kind)} .facet-count`)).toHaveText(String(n))
    }
    await expect(page.locator(`${group('Sessions')} .group-count`)).toHaveText('19')
    await expect(page.locator(`${PANEL} .query-count`)).toHaveText('25 results')
    // Settled, the first row is picked.
    await expect(page.locator(ACTIVE)).toHaveCount(1)

    // Refining the query empties every group until it answers again: the
    // previous query's rows are not selectable under the new one.
    await page.keyboard.press('ArrowDown')
    await page.unrouteAll({ behavior: 'ignoreErrors' })
    await page.route('**/api/search?**', () => new Promise(() => {}))
    await page.locator(INPUT).fill('paletted')
    await expect(page.locator(`${facet('session')} .facet-count`)).toHaveText('…')
    await expect(page.locator(ACTIVE)).toHaveCount(0)

    // Offline: nothing could be asked, so every count is unknown.
    await page.unrouteAll({ behavior: 'ignoreErrors' })
    await page.route('**/api/search?**', route => route.abort())
    await page.locator(INPUT).fill('palettes')
    await expect(page.locator(`${PANEL} [data-testid="search-offline"]`)).toBeVisible()
    await expect(page.locator(`${facet('session')} .facet-count`)).toHaveText('—')
    await expect(page.locator(`${facet('all')} .facet-count`)).toHaveText('—')
    await expect(page.locator(`${PANEL} .query-count`)).toHaveText('offline')
    await expect(page.locator(INPUT)).toHaveValue('palettes')
  })

  test('test_tab_skips_empty_kinds', async ({ page }) => {
    await mockSearch(page)
    await openOn(page, 'palette')
    await expect(page.locator(`${facet('defect')} .facet-count`)).toHaveText('0')
    await expect(page.locator(facet('defect'))).toBeDisabled()
    const activeFacet = page.locator(`${PANEL} .facet.is-active`)
    await expect(activeFacet).toHaveAttribute('data-kind', 'all')
    const order = []
    for (let n = 0; n < 6; n += 1) {
      await page.keyboard.press('Tab')
      order.push(await activeFacet.getAttribute('data-kind'))
    }
    expect(order).toEqual(['feature', 'task', 'session', 'pattern', 'file', 'all'])
    await page.keyboard.press('Shift+Tab')
    await expect(activeFacet).toHaveAttribute('data-kind', 'file')
  })

  test('test_address_miss_note', async ({ page }) => {
    const searched = []
    await page.route('**/api/search?**', (route) => {
      searched.push(route.request().url())
      return route.continue()
    })
    // A kind left picked by an earlier search must not turn the address into
    // a text search for its digits.
    await page.addInitScript(() => localStorage.setItem('regin.search.facet', 'Tasks'))
    await openOn(page, 'T987654321')
    const note = page.locator(`${PANEL} [data-testid="address-miss"]`)
    await expect(note).toContainText('No task T987654321')
    await expect(note).toContainText('No text mentions “987654321” either.')
    // A miss never offers the nearest ids in its place.
    await expect(page.locator(OPTION)).toHaveCount(0)
    await expect(page.locator(`${PANEL} .query-count`)).toHaveText('0 resolved')
    // An address is resolved, not searched as text.
    expect(searched).toHaveLength(0)
  })
})
