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
    await expect(
      page.locator(`${PANEL} .facet.is-active`),
    ).toHaveText(/Sessions/)
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
    // A session names itself late, so the fallback is the trace id — and the
    // fallback must never render a string that is not a visible prefix of the
    // real id. `manual-gate` sliced to 8 reads `manual-g`, which resolves to
    // nothing and is indistinguishable from a real short id.
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
    const row = page.locator(`${ROW}[data-trace-id="${short}"] .switcher-name`)
    await expect(row).toBeVisible()
    // Truncated with a visible marker, never silently mangled.
    await expect(row).toHaveText(`${short.slice(0, 8)}\u2026`)
  })

  test('the preview leads with what a session wants', async ({ page }) => {
    await seedSessions(page)
    await page.goto('/trace/sessions')
    await openSwitcher(page)
    // One view only — a role-labelled tail, no raw/terminal toggle.
    await expect(page.locator(`${PANEL} .switcher-chat`)).toBeVisible()
    await expect(page.locator(`${PANEL} .switcher-turn`).first()).toBeVisible()
    await expect(page.getByTestId('switcher-attach')).toContainText('Attach')
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
