/**
 * The palette's empty query (feature #146, R1 Home / R8 ⌃S, Screens 1 and 9).
 *
 * `/api/palette/home` is answered from a fixture: what these tests pin is the
 * desk's order, caps and wording over the server's groups, and the scratch DB
 * cannot be made to hold six waiting items on demand. The chain test opens a
 * real seeded trace, because "This session" only exists while one is open.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const PANEL = '[role="dialog"][aria-label="Full-text search"]'
const INPUT = `${PANEL} .query-input`
const ACTIVE = `${PANEL} [role="option"][aria-selected="true"]`
const HOME = `${PANEL} .search-home`
const homeGroup = key => `${PANEL} [data-home-group="${key}"]`
const rowsOf = key => `${homeGroup(key)} [role="option"]:not([data-entry])`
const SCOPE = label => `${PANEL} .scope:has-text("${label}")`

const status = (word, bucket) => ({ word, bucket })

function session(ref, title, word = 'working', bucket = 'active', extra = {}) {
  return {
    kind: 'session', id: `${ref.slice(1)}-0000-4000-8000-000000000000`, ref, title, status: status(word, bucket),
    owners: [{ kind: 'feature', id: 146, ref: 'F146', title: '' }],
    context: `prompt for ${title}`, ...extra,
  }
}

function defect(id, title) {
  return {
    kind: 'defect', id, ref: `D${id}`, title, status: status('triage', 'needs_you'),
    owners: [{ kind: 'none', id: null, ref: 'no feature', title: '' }], aside: 'no fix',
  }
}

function feature(id, title) {
  return {
    kind: 'feature', id, ref: `F${id}`, title, status: status('building · round 2', 'active'),
    owners: [], context: '1/5 done · 2 open defects',
  }
}

const WAITING = [
  session('#d8e2b6a1', 'Draft the NOW zone data loader', 'needs you', 'needs_you',
    { aside: 'approval · 18m', context: 'asks: Run the migration?' }),
  session('#c90d4471', 'Make Show all reachable', 'needs you', 'needs_you',
    { aside: 'question · 6m', context: 'asks: option or button?' }),
  defect(281, 'Roster titles untitled sessions with a uuid prefix'),
  defect(284, 'Composer send button double-submits'),
  defect(286, 'Inbox badge stays lit after every item is read'),
]

function desk({ waiting = WAITING, waitingTotal = 6 } = {}) {
  return {
    needs_me: { label: 'Needs me', total: waitingTotal, items: waiting },
    active_now: { label: 'Active now', total: 2, items: [
      session('#7f3e9a12', 'paginate group search'),
      session('#5be21f08', 'Composer keeps its draft on reattach'),
    ] },
    active_features: { label: 'Active features', total: 2, items: [
      feature(146, 'Search Palette'), feature(139, 'Trace follow-up composer'),
    ] },
    sessions: { label: 'Sessions', total: 25, items: [
      session('#a1b2c3d4', 'Write the palette spec', 'ended', 'done'),
      session('#0aa7f3d2', 'Why are the palette tests slow', 'idle', 'quiet'),
      session('#e4410b9c', 'roster backoff', 'inactive', 'quiet'),
      session('#9921ffab', 'Sketch the NOW zone', 'ended', 'done'),
    ] },
    totals: { feature: 3, task: 2, defect: 0, session: 25 },
  }
}

const task = (id, title) => ({
  kind: 'task', id, ref: `T${id}`, title, status: status('idea', 'quiet'), owners: [],
})

// `/api/palette/list/<key>`: the paged list behind a Home group or a kind.
async function mockLists(page, lists) {
  const calls = []
  await page.route('**/api/palette/list/**', async (route) => {
    const url = new URL(route.request().url())
    const key = url.pathname.split('/').pop()
    calls.push(key)
    const items = lists[key] || []
    const offset = Number(url.searchParams.get('offset') || 0)
    const limit = Number(url.searchParams.get('limit') || 12)
    await route.fulfill({ json: {
      key, label: key, total: items.length, offset, items: items.slice(offset, offset + limit),
    } })
  })
  return calls
}

async function mockHome(page, answer) {
  const calls = []
  await page.route('**/api/palette/home**', async (route) => {
    const url = new URL(route.request().url())
    const sessionId = url.searchParams.get('session')
    calls.push(sessionId)
    await route.fulfill({ json: answer(sessionId) })
  })
  return calls
}

async function openFromInbox(page) {
  await page.goto('/inbox')
  await page.locator('body').click()
  await page.keyboard.press('Meta+k')
  await page.waitForSelector(PANEL)
}

async function seedTrace(page, title) {
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const at = i => new Date(Date.now() - (6 - i) * 1000).toISOString().replace('Z', '')
  const res = await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` },
    data: [
      { trace_id: traceId, span_id: `p-${p8}`, parent_id: null, name: 'prompt',
        start_time: at(0), attributes: { text: title } },
      { trace_id: traceId, span_id: `a-${p8}`, parent_id: `p-${p8}`,
        name: 'assistant_response', start_time: at(1), duration_ms: 40,
        attributes: { text: `answer for ${title}` } },
    ],
  })
  expect(res.ok()).toBeTruthy()
  return traceId
}

test.describe('search palette — Home', () => {
  test('test_needs_me_is_first', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem(
      'regin.search.recents', JSON.stringify(['palette', 'limit=6'])))
    await mockHome(page, () => desk())
    await mockLists(page, { needs_me: [...WAITING, defect(290, 'Sixth waiting item')] })
    await openFromInbox(page)

    await expect(page.locator(`${HOME}[data-home="desk"]`)).toBeVisible()
    const order = await page.locator(`${PANEL} [data-home-group]`)
      .evaluateAll(els => els.map(e => e.dataset.homeGroup))
    expect(order).toEqual(['needs_me', 'active_now', 'active_features', 'sessions'])

    // Tinted, capped at five, the sixth reachable through "Show all".
    await expect(page.locator(homeGroup('needs_me'))).toHaveClass(/home-needs/)
    await expect(page.locator(rowsOf('needs_me'))).toHaveCount(5)
    const showAll = page.locator(`${homeGroup('needs_me')} [data-entry="more"]`)
    await expect(showAll).toHaveText(/Show all 6 waiting →/)
    await expect(page.locator(`${PANEL} .query-count`)).toHaveText('6 waiting on you')
    await expect(page.locator(`${homeGroup('sessions')} [data-entry="more"]`))
      .toHaveText(/Show all 25 sessions →/)

    // The cursor starts on the oldest wait, and ↓ alone reaches "Show all".
    await expect(page.locator(ACTIVE)).toContainText('#d8e2b6a1')
    for (let n = 0; n < 5; n += 1) await page.keyboard.press('ArrowDown')
    await expect(page.locator(ACTIVE)).toHaveText(/Show all 6 waiting →/)

    // Recent searches sit in the rail while the query is empty.
    await expect(page.locator(`${PANEL} .rail-label`, { hasText: 'Recent searches' }))
      .toBeVisible()
    await expect(page.locator(`${PANEL} .facet.is-recent`)).toHaveCount(2)

    // ↵ on "Show all" pages every waiting item, the sixth included; esc is
    // the way back to the desk.
    await page.keyboard.press('Enter')
    await expect(page.locator(HOME)).toHaveCount(0)
    await expect(page.locator(`${PANEL} .drill-back`)).toHaveText('← Home')
    await expect(page.locator(`${PANEL} .drill-head .drill-count`)).toHaveText('6')
    await expect(page.locator(`${PANEL} [role="option"]:not([data-entry])`)).toHaveCount(6)
    await expect(page.locator(`${PANEL} [role="option"]`, { hasText: 'D290' })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.locator(`${HOME}[data-home="desk"]`)).toBeVisible()
  })

  test('an empty-query kind pick lists that kind, counted from the desk', async ({ page }) => {
    await mockHome(page, () => desk())
    const calls = await mockLists(page, {
      feature: [feature(146, 'Search Palette'), feature(139, 'Composer'), feature(12, 'Old')],
      task: [task(430, 'Make Show all reachable'), task(431, 'Second task')],
    })
    await openFromInbox(page)
    await expect(page.locator(homeGroup('needs_me'))).toBeVisible()

    await expect(page.locator(`${PANEL} .facet[data-kind="feature"] .facet-count`)).toHaveText('3')
    await expect(page.locator(`${PANEL} .facet[data-kind="task"] .facet-count`)).toHaveText('2')
    await expect(page.locator(`${PANEL} .facet[data-kind="defect"]`)).toBeDisabled()

    const listed = page.locator(`${PANEL} [role="option"]:not([data-entry])`)
    await page.locator(`${PANEL} .facet[data-kind="feature"]`).click()
    await expect(page.locator(HOME)).toHaveCount(0)
    await expect(listed).toHaveCount(3)
    await expect(listed.first()).toContainText('F146')

    await page.locator(`${PANEL} .facet[data-kind="task"]`).click()
    await expect(listed).toHaveCount(2)
    await expect(listed.first()).toContainText('T430')
    await expect(page.locator(`${PANEL} .drill-end`)).toHaveText('end of list · all 2 shown')
    expect(calls).toEqual(['feature', 'task'])

    await page.keyboard.press('Escape')
    await expect(page.locator(`${HOME}[data-home="desk"]`)).toBeVisible()
  })

  test('test_calm_home_collapses_needs_me', async ({ page }) => {
    await mockHome(page, () => desk({ waiting: [], waitingTotal: 0 }))
    await openFromInbox(page)

    const needs = page.locator(homeGroup('needs_me'))
    await expect(needs).toBeVisible()
    await expect(needs).not.toHaveClass(/home-needs/)
    await expect(needs.locator('[role="option"]')).toHaveCount(0)
    await expect(needs.locator('.home-calm'))
      .toHaveText('Nothing is waiting on you. Active work is below.')
    await expect(page.locator(`${PANEL} .query-count`)).toHaveText('nothing waiting')
    // With nothing waiting the cursor starts on the first active session.
    await expect(page.locator(ACTIVE)).toContainText('#7f3e9a12')

    // Typing leaves Home for the search it asked for.
    await page.locator(INPUT).fill('palette')
    await expect(page.locator(HOME)).toHaveCount(0)
  })

  test('test_this_session_shows_chain', async ({ page }) => {
    const traceId = await seedTrace(page, 'home chain fixture')
    const p8 = traceId.slice(0, 8)
    const calls = await mockHome(page, sessionId => (sessionId ? { chain: [
      session(`#${p8}`, 'Make Show all reachable', 'needs you', 'needs_you',
        { here: true, aside: 'question · 6m' }),
      feature(146, 'Search Palette'),
      { kind: 'task', ref: 'T430', title: 'Make Show all reachable by keyboard',
        status: status('needs you', 'needs_you'), owners: [], context: 'run 2 · stopped' },
      defect(276, '+N more under a search group cannot be reached'),
    ] } : desk()))
    await page.addInitScript(() => localStorage.setItem('regin.search.scope', 'session'))
    await page.goto(`/trace/sessions/${traceId}?view=conversation`)
    await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
    await page.keyboard.press('Meta+k')
    await page.waitForSelector(PANEL)

    await expect(page.locator(SCOPE('This session'))).toHaveAttribute('aria-pressed', 'true')
    await expect(page.locator(`${HOME}[data-home="chain"]`)).toBeVisible()
    await expect(page.locator(`${PANEL} .home-inside-title`)).toHaveText(`Searching inside #${p8}`)
    const chain = page.locator(homeGroup('chain'))
    await expect(chain.locator('.group-label')).toHaveText("This session's chain")
    await expect(chain.locator('[role="option"]')).toHaveCount(4)
    await expect(chain.locator('[role="option"]').first().locator('.pr-here')).toHaveText('HERE')
    await expect(page.locator(`${PANEL} .query-count`)).toHaveText('this session')
    expect(calls).toContain(traceId)

    // ⌃S widens to the desk.
    await page.keyboard.press('Control+s')
    await expect(page.locator(`${HOME}[data-home="desk"]`)).toBeVisible()
    await expect(page.locator(homeGroup('needs_me'))).toBeVisible()
  })

  test('test_ctrl_s_without_session_is_noop', async ({ page }) => {
    const calls = await mockHome(page, () => desk())
    await openFromInbox(page)
    await expect(page.locator(homeGroup('needs_me'))).toBeVisible()

    await expect(page.locator(SCOPE('This session'))).toBeDisabled()
    await page.keyboard.press('Control+s')
    await expect(page.locator(SCOPE('Everywhere'))).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByTestId('scope-notice')).toContainText('No session is open')
    // The desk is still the desk: no chain was asked for.
    await expect(page.locator(`${HOME}[data-home="desk"]`)).toBeVisible()
    expect(calls.every(c => c === null)).toBe(true)

    // The notice describes the keystroke just made, and the next one clears it.
    await page.locator(INPUT).fill('pa')
    await expect(page.getByTestId('scope-notice')).toHaveCount(0)
  })

  test('a failed desk offers a retry, and a facet pick does not re-ask it', async ({ page }) => {
    let fails = 1
    const calls = []
    await page.route('**/api/palette/home**', async (route) => {
      calls.push(route.request().url())
      if (fails > 0) {
        fails -= 1
        await route.fulfill({ status: 500, json: { error: 'boom' } })
        return
      }
      await route.fulfill({ json: desk() })
    })
    await openFromInbox(page)
    await expect(page.locator(`${HOME} .home-failed`)).toContainText("Couldn't load your desk")
    await expect(page.locator(ACTIVE)).toHaveCount(0)
    await page.locator(`${HOME} .home-failed button`).click()
    await expect(page.locator(homeGroup('needs_me'))).toBeVisible()
    const asked = calls.length

    await mockLists(page, { feature: [feature(146, 'Search Palette')] })
    await page.locator(`${PANEL} .facet[data-kind="feature"]`).click()
    await expect(page.locator(`${PANEL} .drill-back`)).toHaveText('← Home')
    await page.locator(`${PANEL} .drill-back`).click()
    await expect(page.locator(homeGroup('needs_me'))).toBeVisible()
    expect(calls.length).toBe(asked)

    // One character is too short to search and is not the empty query either.
    await page.locator(INPUT).fill('p')
    await expect(page.locator(HOME)).toHaveCount(0)
    await expect(page.locator(`${PANEL} .await-query`)).toBeVisible()
  })
})
