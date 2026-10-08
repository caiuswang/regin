/**
 * Session switcher (⌘J) and the trace-view status rail.
 *
 * ⌘J is not a second surface: it is ⌘K with the Sessions facet forced and the
 * query empty. The invariants worth pinning are the ones that distinguish that
 * claim from a second dialog — the palette's own shell, keys and focus trap are
 * untouched; the forcing is an override that does NOT outlive the open (the
 * exact failure the retired ⌘F chord had); and typing a query hands the facet
 * back to the cross-session search it already was.
 *
 * The rail is the ambient half. It is a flex sibling of the content scroller,
 * so "does it cover the trace" is a geometry question with a real answer.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const PANEL = '[role="dialog"][aria-label="Full-text search"]'
const INPUT = `${PANEL} .query-input`
const ROW = `${PANEL} [data-testid="switcher-row"]`
const RAIL = '[data-testid="trace-rail"]'
const CELL = `${RAIL} [data-testid="rail-cell"]`
const SCOPE_KEY = 'regin.search.scope'
const FACET_KEY = 'regin.search.facet'

// Sessions seeded WITHOUT `is_test` — the roster serves the real interactive
// population, which is the whole point of a switcher, so a test-marked fixture
// would never appear in it.
async function seedSessions(page, count = 3) {
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  const ids = []
  const now = Date.now()
  for (let n = 0; n < count; n++) {
    const traceId = randomUUID()
    ids.push(traceId)
    const p8 = traceId.slice(0, 8)
    const at = i => new Date(now - (count - n) * 1000 - (6 - i) * 1000)
      .toISOString().replace('Z', '')
    const spans = [
      {
        trace_id: traceId, span_id: `p-${p8}`, parent_id: null, name: 'prompt',
        start_time: at(0),
        attributes: { text: `switcher fixture session ${n}` },
      },
      {
        trace_id: traceId, span_id: `s1-${p8}`, parent_id: `p-${p8}`,
        name: 'tool.Read', start_time: at(1), duration_ms: 40,
        attributes: { tool_name: 'Read', file_path: `/repo/sw-${n}.py` },
      },
      {
        trace_id: traceId, span_id: `s2-${p8}`, parent_id: `p-${p8}`,
        name: 'assistant_response', start_time: at(2), duration_ms: 40,
        attributes: { text: `switcher fixture answer ${n}` },
      },
    ]
    const res = await page.request.post('/api/session-spans', {
      headers: { Authorization: `Bearer ${token}` }, data: spans,
    })
    expect(res.ok()).toBeTruthy()
  }
  return ids
}

// The chord is bound by the app shell on mount, so pressing it against a page
// that has merely finished loading is a race. Wait for a shell control to
// exist first.
async function ready(page) {
  await expect(page.getByRole('button', { name: /quick search/i }).first())
    .toBeVisible({ timeout: 20_000 })
}

async function openSwitcher(page) {
  await ready(page)
  await page.keyboard.press('Meta+j')
  await page.waitForSelector(PANEL, { timeout: 15_000 })
  await expect(page.locator(ROW).first()).toBeVisible({ timeout: 15_000 })
}

test.describe('session switcher', () => {
  test('⌘J opens the palette on Sessions with an empty query', async ({ page }) => {
    await seedSessions(page)
    await page.goto('/trace/sessions')
    await openSwitcher(page)

    await expect(page.locator(INPUT)).toBeFocused()
    await expect(page.locator(INPUT)).toHaveValue('')
    // The roster has no left rail (R1): nothing to scope or filter by.
    await expect(page.locator(`${PANEL} .facet-rail`)).toHaveCount(0)
    // The shell is the palette's, not a second dialog.
    await expect(page.locator(PANEL)).toHaveCount(1)
    await expect(page.locator(`${PANEL} .query-count`)).toHaveText(/session/)
  })

  test('neither forced axis outlives the open', async ({ page }) => {
    await seedSessions(page)
    await page.goto('/trace/sessions')
    // Pin BOTH stored preferences first, so either leak would be visible.
    // ⌘J forces a scope AND a facet; persisting either is the failure that
    // retired the ⌘F chord, and scope alone is only half the check.
    await page.evaluate(([s, f]) => {
      localStorage.setItem(s, 'session')
      localStorage.setItem(f, 'Files')
    }, [SCOPE_KEY, FACET_KEY])
    // The palette reads both keys once, at module import — writing them into a
    // live page changes storage without changing the state the palette is
    // actually running on, which would make the assertions below vacuous.
    await page.reload()
    await openSwitcher(page)
    await page.keyboard.press('Escape')
    await expect(page.locator(PANEL)).toHaveCount(0)

    expect(await page.evaluate(k => localStorage.getItem(k), SCOPE_KEY))
      .toBe('session')
    expect(await page.evaluate(k => localStorage.getItem(k), FACET_KEY))
      .toBe('Files')

    // And the observable consequence: a later plain ⌘K opens the palette the
    // reader left, not the switcher.
    await ready(page)
    await page.keyboard.press('Meta+k')
    await page.waitForSelector(PANEL)
    await expect(page.locator(`${PANEL} .facet.is-active`)).toHaveText(/Files/)
    await expect(page.locator(ROW)).toHaveCount(0)
  })

  test('tabbing off Sessions is a real choice and persists', async ({ page }) => {
    await seedSessions(page)
    await page.goto('/trace/sessions')
    await openSwitcher(page)
    await page.keyboard.press('Tab')
    const picked = await page.locator(`${PANEL} .facet.is-active`).textContent()
    await page.keyboard.press('Escape')
    expect(await page.evaluate(k => localStorage.getItem(k), FACET_KEY))
      .toBe(picked.replace(/\d+$/, '').trim())
  })

  test('↑↓ previews and never commits; ↵ attaches', async ({ page }) => {
    await seedSessions(page)
    await page.goto('/trace/sessions')
    await openSwitcher(page)

    const before = page.url()
    await page.keyboard.press('ArrowDown')
    // Moving the selection is a preview, exactly like tmux' choose-tree.
    expect(page.url()).toBe(before)
    // The roster is shared state — other specs seed into the same scratch DB —
    // so the assertion is that ↵ lands on the row the cursor is ON, not on a
    // particular session this test happened to create.
    const selected = await page.locator(`${ROW}.is-active`).getAttribute('data-trace-id')
    expect(selected).toBeTruthy()

    await page.keyboard.press('Enter')
    await expect(page.locator(PANEL)).toHaveCount(0)
    await expect(page).toHaveURL(new RegExp(`/trace/sessions/${selected}`))
  })

  test('1–9 attach by number', async ({ page }) => {
    await seedSessions(page)
    await page.goto('/trace/sessions')
    await openSwitcher(page)

    const first = await page.locator(ROW).first().getAttribute('data-trace-id')
    await page.keyboard.press('1')
    await expect(page.locator(PANEL)).toHaveCount(0)
    await expect(page).toHaveURL(new RegExp(`/trace/sessions/${first}`))
  })

  test('typing a query hands the facet back to search', async ({ page }) => {
    await seedSessions(page)
    await page.goto('/trace/sessions')
    await openSwitcher(page)

    await page.locator(INPUT).fill('zz-no-such-session-zz')
    await page.waitForTimeout(1200)
    // The switcher rows are gone; the Sessions facet is the cross-session
    // digest search again.
    await expect(page.locator(ROW)).toHaveCount(0)
    await expect(page.locator(`${PANEL} .facet.is-active`)).toHaveText(/Sessions/)
  })

  test('an untitled row shows an id it can be found by', async ({ page }) => {
    // Every row leads with its `#` ref, so an untitled session's title never
    // repeats the id (R3) — and the ref is a visible prefix of the real id,
    // one `#` lookup can resolve.
    const short = 'sw-short-id'
    await page.goto('/trace/sessions')
    const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
    const at = new Date(Date.now() - 2000).toISOString().replace('Z', '')
    expect((await page.request.post('/api/session-spans', {
      headers: { Authorization: `Bearer ${token}` },
      data: [{
        trace_id: short, span_id: `p-${short}`, parent_id: null,
        name: 'session.start', start_time: at,
        attributes: { cwd: '/tmp/sw-short' },
      }],
    })).ok()).toBeTruthy()

    await openSwitcher(page)
    const row = page.locator(`${ROW}[data-trace-id="${short}"]`)
    await expect(row.locator('.pr-ref')).toHaveText(`#${short.slice(0, 8)}`)
    await expect(row.locator('.pr-title')).toHaveText('untitled · no prompts')
  })

  test('the preview is the same entity preview search shows', async ({ page }) => {
    await seedSessions(page)
    await page.goto('/trace/sessions')
    await openSwitcher(page)
    const preview = page.locator(`${PANEL} .entity-preview[data-kind="session"]`)
    await expect(preview).toBeVisible()
    await expect(preview.locator('[data-action="primary"]')).toContainText('Attach')
    await expect(preview.locator('[data-action="secondary"]')).toContainText('New tab')
    await expect(preview.locator('[data-action="copy"]')).toContainText('Copy id')
  })
})

// The roster answered from fixtures: what these pin is how the client groups,
// numbers and draws rows the server built — `tests/palette/test_rows.py` owns
// the builder itself.
const chip = (kind, id, ref) => ({ kind, id, ref, title: '' })
const ago = s => new Date(Date.now() - s * 1000).toISOString().replace('Z', '')

function rosterRow(traceId, phase, row) {
  return {
    trace_id: traceId, phase, title: row.title, turn: 9, context_pct: 41,
    started_at: ago(1800), last_start_at: ago(1800), last_seen: ago(30),
    tail: [{ ts: ago(30), name: 'assistant_response', kind: 'assistant', text: 'tail line' }],
    row: { kind: 'session', owners: [], ...row },
  }
}

const AWAITING = rosterRow('aaaa1111-0000-4000-8000-000000000001', 'waiting-permission', {
  ref: '#aaaa1111', title: 'Draft the NOW zone data loader',
  status: { word: 'needs you', bucket: 'needs_you' }, owners: [chip('feature', 150, 'F150')],
  context: 'asks: run the migration?', aside: 'approval · 18m',
})
const RETRYING = rosterRow('bbbb2222-0000-4000-8000-000000000002', 'retrying', {
  ref: '#bbbb2222', title: 'paginate group search',
  status: { word: 'reconnecting', bucket: 'active' },
  owners: [chip('feature', 146, 'F146'), chip('task', 421, 'T421')],
  context: 'Return total per group',
})
const WORKING = rosterRow('cccc3333-0000-4000-8000-000000000003', 'working', {
  ref: '#cccc3333', title: 'Composer keeps its draft',
  status: { word: 'working', bucket: 'active' }, owners: [chip('feature', 139, 'F139')],
  context: 'Keep the draft in sessionStorage',
})
const ENDED = rosterRow('dddd4444-0000-4000-8000-000000000004', 'ended', {
  ref: '#dddd4444', title: 'Write the palette spec',
  status: { word: 'ended', bucket: 'done' }, context: 'Draft the brief',
})
const ROSTER = [ENDED, WORKING, RETRYING, AWAITING]

async function fakeRoster(page) {
  await page.route('**/api/session-switcher*', (route) => {
    const id = new URL(route.request().url()).searchParams.get('id')
    const sessions = id ? ROSTER.filter(r => r.trace_id.startsWith(id)) : ROSTER
    route.fulfill({ json: { sessions, server_now: ago(0), server_now_utc: ago(0) } })
  })
  await page.route('**/api/palette/relations/**', (route) => {
    const id = decodeURIComponent(route.request().url().split('/').pop())
    const row = ROSTER.find(r => r.trace_id === id)?.row
    route.fulfill({
      json: { kind: 'session', id, row, relations: [], lists: [], body: '', prompts: [] },
    })
  })
}

async function openFakeRoster(page) {
  await fakeRoster(page)
  await page.goto('/trace/sessions')
  await openSwitcher(page)
}

const rowOf = r => page => page.locator(`${ROW}[data-trace-id="${r.trace_id}"]`)

test.describe('roster rows (F146 S8)', () => {
  test('test_roster_row_names_owners', async ({ page }) => {
    await openFakeRoster(page)
    const row = rowOf(RETRYING)(page)
    await expect(row.locator('.pr-ref')).toHaveText('#bbbb2222')
    await expect(row.locator('.pr-title')).toHaveText('paginate group search')
    await expect(row.locator('.pr-chip')).toHaveText(['F146', 'T421'])
    await expect(row.locator('.pr-status')).toHaveText('reconnecting')
    await expect(row.locator('.pr-context')).toContainText('Return total per group')
    // Two lines, never more.
    await expect(row.locator('.pr-line')).toHaveCount(2)
    await expect(rowOf(AWAITING)(page).locator('.pr-chip')).toHaveText(['F150'])
    await expect(page.locator(`${PANEL} .facet-rail`)).toHaveCount(0)
  })

  test('test_roster_number_attach_kept', async ({ page }) => {
    await openFakeRoster(page)
    // Server rank, not recency: awaiting, retrying, working, ended.
    await expect(page.locator(`${ROW} .switcher-hotkey`)).toHaveText(['1', '2', '3', '4'])

    // `#` addresses by id and gives the digits back to the query.
    await page.locator(INPUT).fill('#bbbb')
    await expect(page.locator(ROW)).toHaveCount(1)
    await expect(page.locator(`${ROW} .switcher-hotkey`)).toHaveText([''])
    await page.locator(INPUT).fill('')
    await expect(page.locator(ROW)).toHaveCount(4)

    await page.keyboard.press('2')
    await expect(page.locator(PANEL)).toHaveCount(0)
    await expect(page).toHaveURL(new RegExp(`/trace/sessions/${RETRYING.trace_id}`))
  })

  test('picking Sessions in ⌘K keeps the rail', async ({ page }) => {
    await fakeRoster(page)
    await page.goto('/trace/sessions')
    await ready(page)
    await page.keyboard.press('Meta+k')
    await page.waitForSelector(PANEL)
    await page.locator(INPUT).fill('')
    await page.locator(`${PANEL} .facet`, { hasText: 'Sessions' }).click()
    await expect(page.locator(ROW).first()).toBeVisible({ timeout: 15_000 })
    await expect(page.locator(`${PANEL} .facet-rail`)).toBeVisible()
    await expect(page.locator(`${PANEL} .roster-foot`)).toHaveCount(0)
  })

  test('a row the server could not build still renders', async ({ page }) => {
    const bare = { ...ENDED, trace_id: 'eeee5555-0000-4000-8000-000000000005', title: null, row: null }
    ROSTER.push(bare)
    try {
      await openFakeRoster(page)
      const row = rowOf(bare)(page)
      await expect(row.locator('.pr-ref')).toHaveText('#eeee5555')
      await expect(row.locator('.pr-title')).toHaveText('untitled · no prompts')
      await expect(row.locator('.pr-dot')).toHaveAttribute('data-bucket', 'done')
    } finally {
      ROSTER.pop()
    }
  })

  test('test_retrying_counts_active', async ({ page }) => {
    await openFakeRoster(page)
    await expect(page.locator(`${PANEL} .group-head .group-label`))
      .toHaveText(['Needs you', 'Active', 'Recent'])
    await expect(rowOf(RETRYING)(page)).toHaveAttribute('data-group', 'active')
    await expect(rowOf(AWAITING)(page)).toHaveAttribute('data-group', 'needs_you')
    await expect(page.locator(`${PANEL} .group-head[data-group="needs_you"] .group-count`))
      .toHaveText('1')
    await expect(page.locator(`${PANEL} .group-head[data-group="active"] .group-count`))
      .toHaveText('2')
  })

  test('test_narrow_opens_sheet_first', async ({ page }) => {
    await fakeRoster(page)
    await page.goto('/trace/sessions')
    // The shell's search button collapses away at 760px, so wait for it first.
    await ready(page)
    await page.setViewportSize({ width: 760, height: 900 })

    // Search lays the rail down as a horizontal scope + kind strip.
    await page.keyboard.press('Meta+k')
    await page.waitForSelector(PANEL)
    const rail = page.locator(`${PANEL} .facet-rail`)
    await expect(rail).toBeVisible()
    expect(await rail.evaluate(el => getComputedStyle(el).flexDirection)).toBe('row')
    await page.keyboard.press('Escape')
    await expect(page.locator(PANEL)).toHaveCount(0)

    await page.keyboard.press('Meta+j')
    await expect(page.locator(ROW).first()).toBeVisible({ timeout: 15_000 })
    const preview = page.locator(`${PANEL} .entity-preview`)
    const list = page.locator(`${PANEL} .switcher-list`)
    await expect(preview).toBeHidden()
    const before = page.url()

    // ↵ opens the sheet; it does not attach.
    await page.keyboard.press('Enter')
    await expect(preview).toBeVisible()
    await expect(list).toBeHidden()
    expect(page.url()).toBe(before)

    // esc closes only the sheet; → reopens it.
    await page.keyboard.press('Escape')
    await expect(page.locator(PANEL)).toHaveCount(1)
    await expect(preview).toBeHidden()
    await page.keyboard.press('ArrowRight')
    await expect(preview).toBeVisible()

    // With the sheet up, ↵ attaches the highlighted session.
    await page.keyboard.press('Enter')
    await expect(page.locator(PANEL)).toHaveCount(0)
    await expect(page).toHaveURL(new RegExp(`/trace/sessions/${AWAITING.trace_id}`))
  })
})

test.describe('trace status rail', () => {
  test('renders under a trace view and never covers it', async ({ page }) => {
    const ids = await seedSessions(page)
    await page.goto(`/trace/sessions/${ids[0]}?view=conversation`)
    await expect(page.locator(RAIL)).toBeVisible({ timeout: 15_000 })

    const gap = await page.evaluate(() => {
      const rail = document.querySelector('[data-testid="trace-rail"]')
      const scroller = document.querySelector('.content-scroll')
      return rail.getBoundingClientRect().top
        - scroller.getBoundingClientRect().bottom
    })
    // A flex sibling, not an overlay: the rail starts at or below where the
    // scroller ends.
    expect(gap).toBeGreaterThanOrEqual(-1)
  })

  test('a cell attaches, and the attached one is marked', async ({ page }) => {
    const ids = await seedSessions(page)
    await page.goto(`/trace/sessions/${ids[0]}?view=conversation`)
    await expect(page.locator(RAIL)).toBeVisible({ timeout: 15_000 })

    // Whichever session the rail actually offers first — the roster is capped
    // and shared, so naming one of this test's own ids would be asserting
    // about scheduling rather than about the rail.
    const target = await page.locator(CELL).first().getAttribute('data-trace-id')
    expect(target).toBeTruthy()
    await page.locator(`${CELL}[data-trace-id="${target}"]`).click()
    await expect(page).toHaveURL(new RegExp(`/trace/sessions/${target}`))

    // Attached is marked by state, never by a different hue.
    await expect(page.locator(`${CELL}[data-trace-id="${target}"]`))
      .toHaveAttribute('aria-current', 'page')
    await expect(page.locator(`${CELL}[aria-current="page"]`)).toHaveCount(1)
  })

  test('is absent off a trace view', async ({ page }) => {
    await seedSessions(page)
    await page.goto('/trace/sessions')
    await ready(page)
    await expect(page.locator(RAIL)).toHaveCount(0)
  })
})
