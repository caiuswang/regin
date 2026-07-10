/**
 * Live mobile card (`/live/:id?`) — acceptance tests for
 * docs/mobile-progress-card-design.md.
 *
 * Written AHEAD of the component landing (a parallel agent is building the
 * view on this same branch). Every assertion is coded against the APPROVED
 * DOM contract only (data-testids: live-card, live-header, live-goal,
 * live-fold, live-tail, live-row[data-kind][data-span-id], live-newchip,
 * live-now[data-state], live-sheet, live-sheet-copy, live-filter,
 * live-toggle-system) — nothing here reaches into `frontend/src/`. Until the
 * route exists these tests are EXPECTED to fail/timeout; that is the
 * contract, not a bug in this file (see the task's own gate: "file parses,
 * collects, follows conventions").
 *
 * Fixtures:
 *  - the real heavy session 8e964958-dbd1-4af1-86be-c1c845cec291 — the
 *    fixture that motivated the signal filter (design doc, "v4 addition") —
 *    for the tests where realistic volume/shape matters: the 375px
 *    invariant, the default signal filter, and the fold-count math;
 *  - synthetic `is_test: true` sessions posted via `/api/session-spans`
 *    (same convention as trace-closed-session-poll-stop.spec.js,
 *    trace-live-prompt-handoff.spec.js, workflow-span-folding.spec.js) for
 *    every scenario that needs an exact, deterministic shape: 0/1/N span
 *    counts, ended vs. active poll lifecycle, NOW-zone state priority,
 *    sheets, and the message/activity hierarchy.
 *
 * Viewport: this whole file pins 375x667 (the design doc's explicit "iPhone
 * SE baseline") via `test.use`, not the repo's `mobile` Playwright project —
 * that project's testMatch only covers responsive.spec.js, and (checked
 * against the installed Playwright version) `devices['iPhone SE']` actually
 * resolves to 320x568, not 375x667. playwright.config.js is outside this
 * file's scope (frontend/tests/ only), so the exact width the acceptance
 * checklist asks for is pinned in-file instead.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'
import { contentOverflow, settle, squishedColumns } from './helpers/overflow.js'

const HEAVY_FIXTURE = '8e964958-dbd1-4af1-86be-c1c845cec291'

test.use({ viewport: { width: 375, height: 667 } })

// ---- helpers --------------------------------------------------------------

async function post(page, spans) {
  const res = await page.request.post('/api/session-spans', { data: spans })
  expect(res.ok()).toBeTruthy()
}

async function authHeaders(page) {
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()
  return { Authorization: `Bearer ${token}` }
}

function isMapRequest(url, traceId) {
  return url.includes(`/sessions/${traceId}/map`)
}
function isInitialMap(url, traceId) {
  return isMapRequest(url, traceId) && !url.includes('before_id=') && !url.includes('after_id=')
}

const rows = (page) => page.locator('[data-testid="live-row"]')
const msgRows = (page) => page.locator('[data-testid="live-row"][data-kind="msg"]')
const actRows = (page) => page.locator('[data-testid="live-row"][data-kind="act"]')

function parseLeadingNumber(text) {
  const m = (text || '').match(/(\d[\d,]*)/)
  return m ? parseInt(m[1].replace(/,/g, ''), 10) : NaN
}

// ---- acceptance #1: 375px invariant ---------------------------------------

test.describe('375px invariant (acceptance #1)', () => {
  test('worst-case fixture: no horizontal overflow, no squished columns', async ({ page }) => {
    await page.goto(`/live/${HEAVY_FIXTURE}`)
    await settle(page)

    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })

    const m = await contentOverflow(page)
    test.skip(!m.pane, 'content pane not present (redirected to login/other)')
    expect(
      m.scrollWidth,
      `/live overflows (${m.scrollWidth} > ${m.clientWidth}); offenders: ${m.offenders.join(', ') || 'unknown'}`
    ).toBeLessThanOrEqual(m.clientWidth + 1)

    const squished = await squishedColumns(page)
    expect(squished, `/live squished columns: ${squished.join(' | ')}`).toEqual([])
  })

  test('long command_preview / file-path text wraps instead of overflowing', async ({ page }) => {
    await page.goto(`/live/${HEAVY_FIXTURE}`)
    await settle(page)
    await expect(rows(page).first()).toBeVisible({ timeout: 10_000 })

    // Design doc geometry section: long unbroken strings get
    // `overflow-wrap: anywhere`. Measure the INNER text nodes
    // (.live-msg-body / .live-row-main) — the live-row container itself is a
    // button and computes overflow-wrap 'normal'; the wrap rule lives on the
    // text elements. Best-effort — only asserts when a text node with a long
    // unbroken token (a `$ <command>` mono line, per the row-language spec)
    // is actually present in this fixture's loaded window.
    const wrapping = await page.evaluate(() => {
      const textEls = [...document.querySelectorAll(
        '[data-testid="live-row"] .live-msg-body, [data-testid="live-row"] .live-row-main')]
      for (const el of textEls) {
        const text = el.textContent || ''
        if (/\$\s*\S{20,}/.test(text) || /\/\S{25,}/.test(text)) {
          return getComputedStyle(el).overflowWrap
        }
      }
      return null
    })
    if (wrapping !== null) {
      expect(['anywhere', 'break-word'], `overflow-wrap was "${wrapping}"`).toContain(wrapping)
    }
  })
})

// ---- acceptance #8c: default signal filter --------------------------------

test.describe('Default signal filter (acceptance #8c)', () => {
  test('system span language never renders by default on the worst-case fixture', async ({ page }) => {
    await page.goto(`/live/${HEAVY_FIXTURE}`)
    await settle(page)
    await expect(rows(page).first()).toBeVisible({ timeout: 10_000 })

    const texts = await rows(page).allTextContents()
    expect(texts.length).toBeGreaterThan(0)

    const systemWordRe = /\b(turn|hook\.|harness\.|config\.change)\b/
    const leakedSystemWords = texts.filter((t) => systemWordRe.test(t))
    expect(leakedSystemWords, `rows leaking system span language: ${JSON.stringify(leakedSystemWords)}`)
      .toEqual([])

    const leakedRawPrefixes = texts.filter((t) => t.includes('tool.') || t.includes('pre_tool.'))
    expect(leakedRawPrefixes, `rows leaking raw span-type prefixes: ${JSON.stringify(leakedRawPrefixes)}`)
      .toEqual([])
  })
})

// ---- acceptance #2: fold row 0 / <=5 turns / N turns (v7.2 anchor-paged) ---
//
// v7.2 correction (design doc "Data + lifecycle"): the shallow map's `limit`
// pages TURN ANCHORS (prompt/compact/session spans), not individual spans.
// The card's initial window is limit=5 anchors + child hydration; the fold
// row's count stays in SPANS (= span_count_total − loaded spans), and each
// unfold loads 5 more turns.

test.describe('Fold row — 0 / <=5 turns / N turns (acceptance #2, v7.2)', () => {
  test('0 spans: header renders, no fold row, "no spans yet"', async ({ page }) => {
    const traceId = randomUUID() // never posted -> genuinely 0 spans

    await page.goto(`/live/${traceId}`)
    await settle(page)

    await expect(page.locator('[data-testid="live-header"]')).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[data-testid="live-fold"]')).toHaveCount(0)
    await expect(rows(page)).toHaveCount(0)
    await expect(page.getByText(/no spans yet/i)).toBeVisible()
  })

  test('<=5 turns: no fold row rendered', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'small fixture prompt', is_test: true } },
      { trace_id: traceId, span_id: `bash-${sfx}`, parent_id: null, name: 'tool.Bash',
        start_time: now, attributes: { command_preview: 'echo hi', is_test: true } },
      { trace_id: traceId, span_id: `end-${sfx}`, parent_id: null, name: 'session.end',
        start_time: now, attributes: { reason: 'clear', is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(rows(page).first()).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[data-testid="live-fold"]')).toHaveCount(0)
  })

  test('>5 turns: fold count = span_count_total - loaded spans, unfold prepends a turn page and anchors scroll, repeat decrements', async ({ page }) => {
    let initialMap = null
    page.on('response', async (resp) => {
      if (resp.request().method() !== 'GET' || initialMap) return
      if (!isInitialMap(resp.url(), HEAVY_FIXTURE)) return
      try { initialMap = await resp.json() } catch { /* ignore */ }
    })

    await page.goto(`/live/${HEAVY_FIXTURE}`)
    await settle(page)
    await expect.poll(() => initialMap !== null, { timeout: 10_000 }).toBeTruthy()

    // The heavy fixture has ~19 prompts >> the 5-turn initial window, so the
    // fold row MUST render (v7.2 acceptance item 2).
    const fold = page.locator('[data-testid="live-fold"]')
    await expect(fold).toBeVisible({ timeout: 10_000 })

    // Both terms come from the SAME network response the page made: the
    // remainder is counted in SPANS even though pagination is in turns.
    const remainder = initialMap.span_count_total - initialMap.span_count
    expect(remainder, 'fixture must exceed the 5-turn initial window for this test to be meaningful').toBeGreaterThan(0)
    const foldNum = parseLeadingNumber(await fold.textContent())
    expect(foldNum, `fold row text vs API remainder ${remainder}`).toBe(remainder)

    // Scroll to the TOP first — how a user actually reaches the fold row.
    // This also unpins follow-tail, so a live append during the measurement
    // can't auto-scroll the tail and pollute the anchor comparison.
    const tail = page.locator('[data-testid="live-tail"]')
    await tail.evaluate((el) => { el.scrollTop = 0 })
    await page.waitForTimeout(150)

    // Anchor the oldest loaded row: the click must PREPEND the older turn
    // page without moving this row on screen. Measure via
    // getBoundingClientRect directly — Playwright's boundingBox() returns
    // null for rows clipped outside the tail's scrollport.
    const anchorId = await rows(page).first().getAttribute('data-span-id')
    const rowTop = (id) => page.evaluate((spanId) => {
      const el = document.querySelector(`[data-testid="live-row"][data-span-id="${spanId}"]`)
      return el ? el.getBoundingClientRect().top : null
    }, id)
    const beforeTop = await rowTop(anchorId)
    expect(beforeTop, 'anchor row not in the DOM before unfold').not.toBeNull()

    // Wait on the fold COUNTER, not the before_id network response: the
    // unfold stages the older roots + all their children into one atomic
    // merge, so the counter (and DOM prepend) land well after the map
    // response while the per-root children fetches are still in flight.
    const foldCount = async () => parseLeadingNumber(await fold.textContent())
    await fold.click()
    await expect.poll(foldCount, { timeout: 15_000 })
      .toBeLessThan(foldNum)
    await page.waitForTimeout(150) // scroll-anchor restore settles

    const afterTop = await rowTop(anchorId)
    expect(afterTop, 'anchor row lost from the DOM after unfold').not.toBeNull()
    expect(Math.abs(afterTop - beforeTop), 'prepending older spans must not move the anchored row').toBeLessThanOrEqual(2)

    // Repeat: a second unfold keeps decrementing (monotonic, not a one-shot).
    const foldNum2 = await foldCount()
    await fold.click()
    await expect.poll(foldCount, { timeout: 15_000 })
      .toBeLessThan(foldNum2)
  })
})

// ---- acceptance #4: poll lifecycle -----------------------------------------

test.describe('Poll lifecycle (acceptance #4)', () => {
  test('an ENDED session issues zero further /map requests', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, end_time: now, status_code: 'OK',
        attributes: { text: 'poll-lifecycle ended fixture', is_test: true } },
      { trace_id: traceId, span_id: `resp-${sfx}`, parent_id: null, name: 'assistant_response',
        start_time: now, end_time: now, status_code: 'OK',
        attributes: { text: 'final answer text', is_test: true } },
      { trace_id: traceId, span_id: `end-${sfx}`, parent_id: null, name: 'session.end',
        start_time: now, end_time: now, status_code: 'OK', attributes: { reason: 'clear', is_test: true } },
    ])

    let mapCount = 0
    page.on('request', (req) => {
      if (req.method() === 'GET' && isMapRequest(req.url(), traceId)) mapCount += 1
    })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(rows(page).first()).toBeVisible({ timeout: 10_000 })

    // Let any bounded catch-up settle before the measurement window starts.
    await page.waitForTimeout(1_000)
    const afterLoad = mapCount
    expect(afterLoad).toBeGreaterThanOrEqual(1)

    await page.waitForTimeout(6_000)
    expect(mapCount, 'an ended session must not keep polling /map').toBe(afterLoad)
  })

  // `sessions.last_seen` is set server-side to `datetime('now')` at ingest
  // time (db/schema.sql), independent of the payload's own start_time, and a
  // NULL/unset `status` + recent `last_seen` falls back to "active"
  // (web/blueprints/trace/sessions.py `_apply_filters`). So posting spans
  // with no `session.end` right before navigating reliably produces an
  // active session without touching a real in-flight one — no need to skip.
  test('an ACTIVE session keeps polling /map roughly every 4s', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'poll-lifecycle active fixture', is_test: true } },
    ])

    let mapCount = 0
    page.on('request', (req) => {
      if (req.method() === 'GET' && isMapRequest(req.url(), traceId)) mapCount += 1
    })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(rows(page).first()).toBeVisible({ timeout: 10_000 })

    const afterLoad = mapCount
    // Two poll intervals (~8s) should add at least one more request; a
    // stopped/very-slow poll would not.
    await expect.poll(() => mapCount, { timeout: 10_000, intervals: [500] }).toBeGreaterThan(afterLoad)
  })
})

// ---- acceptance #5 / #8c: filter sheet -------------------------------------

test.describe('Filter sheet — show system spans (acceptance #5 / #8c)', () => {
  test('toggling "show system spans" strictly increases the rendered row count', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'filter sheet fixture', is_test: true } },
      { trace_id: traceId, span_id: `resp-${sfx}`, parent_id: null, name: 'assistant_response',
        start_time: now, attributes: { text: 'a signal response', is_test: true } },
      { trace_id: traceId, span_id: `turn-${sfx}`, parent_id: null, name: 'turn',
        start_time: now, attributes: { is_test: true } },
      { trace_id: traceId, span_id: `hook-${sfx}`, parent_id: null, name: 'hook.pre_tool_use',
        start_time: now, attributes: { is_test: true } },
      { trace_id: traceId, span_id: `cwd-${sfx}`, parent_id: null, name: 'cwd.changed',
        start_time: now, attributes: { is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(rows(page).first()).toBeVisible({ timeout: 10_000 })
    const before = await rows(page).count()

    await page.locator('[data-testid="live-filter"]').click()
    const toggle = page.locator('[data-testid="live-toggle-system"]')
    await expect(toggle).toBeVisible({ timeout: 5_000 })
    await toggle.check()

    await expect.poll(() => rows(page).count(), { timeout: 5_000 })
      .toBeGreaterThan(before)
  })
})

// ---- acceptance #8d: message-first hierarchy -------------------------------

test.describe('Message-first hierarchy (acceptance #8d)', () => {
  test('a message row renders visibly larger (height + font-size) than an adjacent activity row', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'hierarchy fixture prompt', is_test: true } },
      { trace_id: traceId, span_id: `resp-${sfx}`, parent_id: null, name: 'assistant_response',
        start_time: now,
        attributes: {
          text: 'A full assistant response with enough words to occupy multiple lines of clamped body text so its rendered height clearly differs from a one-line activity row underneath it.',
          is_test: true,
        } },
      { trace_id: traceId, span_id: `read-${sfx}`, parent_id: null, name: 'tool.Read',
        start_time: now, attributes: { file_path: 'src/app.js', is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)

    const msg = msgRows(page).first()
    const act = actRows(page).first()
    await expect(msg).toBeVisible({ timeout: 10_000 })
    await expect(act).toBeVisible({ timeout: 10_000 })

    const msgBox = await msg.boundingBox()
    const actBox = await act.boundingBox()
    expect(msgBox.height, `message row (${msgBox.height}px) must be visibly taller than an activity row (${actBox.height}px)`)
      .toBeGreaterThan(actBox.height)

    // Font-size lives on the inner text nodes (.live-msg-body 13px vs
    // .live-row-main 11px) — the row buttons themselves both inherit the
    // same size, so comparing the containers would be a false negative.
    const msgFont = parseFloat(await msg.locator('.live-msg-body')
      .evaluate((el) => getComputedStyle(el).fontSize))
    const actFont = parseFloat(await act.locator('.live-row-main')
      .evaluate((el) => getComputedStyle(el).fontSize))
    expect(msgFont, `message font-size ${msgFont}px must exceed activity font-size ${actFont}px`).toBeGreaterThan(actFont)
  })

  test('tapping a message opens its full text; tapping an activity row opens its attributes', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'hierarchy tap fixture', is_test: true } },
      { trace_id: traceId, span_id: `resp-${sfx}`, parent_id: null, name: 'assistant_response',
        start_time: now, attributes: { text: 'MESSAGE_SHEET_MARKER full text', is_test: true } },
      { trace_id: traceId, span_id: `bash-${sfx}`, parent_id: null, name: 'tool.Bash',
        start_time: now, attributes: { command_preview: 'echo ACTIVITY_SHEET_MARKER', is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)

    // Target the RESPONSE row explicitly — msgRows().first() is the PROMPT
    // row ('hierarchy tap fixture'), whose sheet would not carry the marker.
    await msgRows(page).filter({ hasText: 'MESSAGE_SHEET_MARKER' }).first().click()
    const sheet = page.locator('[data-testid="live-sheet"]')
    await expect(sheet).toBeVisible({ timeout: 5_000 })
    await expect(sheet).toContainText('MESSAGE_SHEET_MARKER')
    await page.keyboard.press('Escape')
    await expect(sheet).toBeHidden({ timeout: 5_000 })

    await actRows(page).first().click()
    await expect(sheet).toBeVisible({ timeout: 5_000 })
    await expect(sheet).toContainText('ACTIVITY_SHEET_MARKER')
  })
})

// ---- acceptance #6: bottom sheets -------------------------------------------

test.describe('Bottom sheets (acceptance #6)', () => {
  test('tapping a message row opens the FULL text (not a truncated preview / attrs list), and copy flips to "Copied"', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    const LONG_TEXT = 'Sheet fixture line one. '
      + 'Filler sentence to push well past any 2-4 line preview clamp. '.repeat(12)
      + 'TAIL_MARKER_UNIQUE_END.'
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'sheet fixture prompt', is_test: true } },
      { trace_id: traceId, span_id: `resp-${sfx}`, parent_id: null, name: 'assistant_response',
        start_time: now, attributes: { text: LONG_TEXT, is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)

    // The RESPONSE row, not .first() (that's the prompt row). The row's
    // clamped preview still starts with the response's opening line, so
    // filtering on it selects the right row.
    const msg = msgRows(page).filter({ hasText: 'Sheet fixture line one' }).first()
    await expect(msg).toBeVisible({ timeout: 10_000 })
    await msg.click()

    const sheet = page.locator('[data-testid="live-sheet"]')
    await expect(sheet).toBeVisible({ timeout: 5_000 })
    // The tail marker only survives if the sheet renders the FULL text
    // rather than the row's clamped/truncated preview.
    await expect(sheet).toContainText('TAIL_MARKER_UNIQUE_END')

    const copyBtn = page.locator('[data-testid="live-sheet-copy"]')
    await expect(copyBtn).toBeVisible()
    await copyBtn.click()
    await expect(copyBtn).toHaveText(/Copied/, { timeout: 2_000 })
  })

  test('an activity row lazy-fetches and renders its full attributes', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'activity sheet fixture', is_test: true } },
      { trace_id: traceId, span_id: `bash-${sfx}`, parent_id: null, name: 'tool.Bash',
        start_time: now, attributes: { command_preview: 'echo ACTIVITY_ATTR_MARKER', is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)

    const act = actRows(page).first()
    await expect(act).toBeVisible({ timeout: 10_000 })
    await act.click()

    const sheet = page.locator('[data-testid="live-sheet"]')
    await expect(sheet).toBeVisible({ timeout: 5_000 })
    await expect(sheet).toContainText('ACTIVITY_ATTR_MARKER')
  })

  test('dismissing a sheet restores the tail scroll position', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    const spans = [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'scroll-restore fixture', is_test: true } },
    ]
    for (let i = 0; i < 40; i++) {
      spans.push({
        trace_id: traceId, span_id: `read-${sfx}-${i}`, parent_id: null, name: 'tool.Read',
        start_time: now, attributes: { file_path: `src/file${i}.js`, is_test: true },
      })
    }
    await post(page, spans)

    await page.goto(`/live/${traceId}`)
    await settle(page)

    const tail = page.locator('[data-testid="live-tail"]')
    await expect(tail).toBeVisible({ timeout: 10_000 })
    await expect(rows(page).first()).toBeVisible()

    // Pick a MID-LIST row and scroll it into view FIRST — clicking an
    // off-screen row would trigger Playwright's actionability auto-scroll
    // AFTER we record scrollTop, polluting the restore target. Scrolling a
    // middle row into view also lands the tail at a non-trivial, non-zero
    // position (the card boots pinned to the bottom).
    const target = actRows(page).nth(15)
    await target.scrollIntoViewIfNeeded()
    await page.waitForTimeout(150)
    const scrollBefore = await tail.evaluate((el) => el.scrollTop)
    expect(scrollBefore).toBeGreaterThan(0)

    await target.click()
    const sheet = page.locator('[data-testid="live-sheet"]')
    await expect(sheet).toBeVisible({ timeout: 5_000 })

    await page.keyboard.press('Escape')
    await expect(sheet).toBeHidden({ timeout: 5_000 })

    const scrollAfter = await tail.evaluate((el) => el.scrollTop)
    expect(scrollAfter, 'dismissing the sheet must not move the tail scroll').toBe(scrollBefore)
  })
})

// ---- acceptance #3: live tail incremental arrival --------------------------

test.describe('Live tail incremental arrival (acceptance #3)', () => {
  test('a span landing mid-view shows the "N new" chip while scrolled up, does not move the viewport, and never duplicates rows', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    const spans = [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'live-tail arrival fixture', is_test: true } },
    ]
    for (let i = 0; i < 20; i++) {
      spans.push({ trace_id: traceId, span_id: `read-${sfx}-${i}`, parent_id: null, name: 'tool.Read',
        start_time: now, attributes: { file_path: `src/f${i}.js`, is_test: true } })
    }
    await post(page, spans)

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const tail = page.locator('[data-testid="live-tail"]')
    await expect(rows(page).first()).toBeVisible({ timeout: 10_000 })

    // Scroll away from the bottom (follow-tail off) so the arrival surfaces
    // as a chip instead of auto-appending in view.
    await tail.evaluate((el) => { el.scrollTop = 0 })
    await page.waitForTimeout(150)
    const scrollBefore = await tail.evaluate((el) => el.scrollTop)

    await post(page, [
      { trace_id: traceId, span_id: `newspan-${sfx}`, parent_id: null, name: 'tool.Write',
        start_time: new Date(Date.now() + 1000).toISOString(),
        attributes: { file_path: 'src/new-arrival.js', is_test: true } },
    ])

    const chip = page.locator('[data-testid="live-newchip"]')
    await expect(chip).toBeVisible({ timeout: 8_000 })
    await expect(chip).toContainText('1')

    const scrollAfter = await tail.evaluate((el) => el.scrollTop)
    expect(scrollAfter, 'the chip must not itself move the scrolled-up viewport').toBe(scrollBefore)

    // No duplicate span-id rows after the poll's merge (retired-prune).
    const ids = await rows(page).evaluateAll((els) => els.map((e) => e.getAttribute('data-span-id')))
    expect(new Set(ids).size, 'duplicate span-id rows after a live-tail poll').toBe(ids.length)

    // Tapping the chip jumps to the bottom and reveals the new row.
    await chip.click()
    await expect(page.locator(`[data-testid="live-row"][data-span-id="newspan-${sfx}"]`))
      .toBeVisible({ timeout: 5_000 })
  })
})

// ---- acceptance #8 / #8b: NOW zone ------------------------------------------

test.describe('NOW zone (acceptance #8)', () => {
  test('exists and stays within the <=100px height budget', async ({ page }) => {
    await page.goto(`/live/${HEAVY_FIXTURE}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toBeVisible({ timeout: 10_000 })
    const box = await nowZone.boundingBox()
    expect(box.height, `NOW zone height ${box.height}px exceeds the <=100px budget`).toBeLessThanOrEqual(100)
  })

  test('worst-case fixture: finished state if the session has actually ended by test time', async ({ page }) => {
    // 8e964958 is a real, currently in-progress session (status was 'active'
    // with no ended_at as of writing this spec) — it may or may not have
    // ended by the time this runs. Assert the strict data-state="finished"
    // only when the API itself reports ended_at; otherwise just prove the
    // zone renders one of the five documented states. The strict version of
    // this assertion (guaranteed ended) is the synthetic test right below.
    // Navigate FIRST: authHeaders reads localStorage, which throws a
    // SecurityError on the initial about:blank page.
    await page.goto(`/live/${HEAVY_FIXTURE}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toBeVisible({ timeout: 10_000 })

    const headers = await authHeaders(page)
    const resp = await page.request.get(`/api/sessions/${HEAVY_FIXTURE}/map?shallow=1&limit=1`, { headers })
    expect(resp.ok()).toBeTruthy()
    const body = await resp.json()

    if (body.ended_at) {
      await expect(nowZone).toHaveAttribute('data-state', 'finished')
    } else {
      test.info().annotations.push({
        type: 'note',
        description: '8e964958 had no ended_at at request time — the strict finished-state assertion is covered by the synthetic-ended test instead.',
      })
      await expect(nowZone).toHaveAttribute('data-state', /^(response|tool|permission|prompt|finished)$/)
    }
  })

  test('synthetic ended session: shows the finished state + final response', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'now-zone ended fixture', is_test: true } },
      { trace_id: traceId, span_id: `resp-${sfx}`, parent_id: null, name: 'assistant_response',
        start_time: now, attributes: { text: 'FINAL_RESPONSE_MARKER', is_test: true } },
      { trace_id: traceId, span_id: `end-${sfx}`, parent_id: null, name: 'session.end',
        start_time: now, attributes: { reason: 'clear', is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toBeVisible({ timeout: 10_000 })
    await expect(nowZone).toHaveAttribute('data-state', 'finished')
    await expect(nowZone).toContainText('FINAL_RESPONSE_MARKER')
  })

  test('a PENDING tool span shows the ticking-elapsed "tool" state', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    const toolUseId = `tu-${sfx}`
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'now-zone pending-tool fixture', is_test: true } },
      { trace_id: traceId, span_id: `pending-${toolUseId}`, parent_id: null, name: 'tool.Bash',
        start_time: now, status_code: 'PENDING',
        attributes: { command_preview: 'npx vite build', tool_use_id: toolUseId, is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toBeVisible({ timeout: 10_000 })
    await expect(nowZone).toHaveAttribute('data-state', 'tool')

    // Elapsed ticks client-side off start_time — must change over ~2s.
    const t1 = await nowZone.textContent()
    await page.waitForTimeout(2_000)
    const t2 = await nowZone.textContent()
    expect(t2, 'NOW zone elapsed time must tick, not stay frozen').not.toBe(t1)
  })

  test('a PENDING permission.request outranks a concurrent PENDING tool', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    const toolUseId = `tu-${sfx}`
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'now-zone permission fixture', is_test: true } },
      { trace_id: traceId, span_id: `pending-${toolUseId}`, parent_id: null, name: 'tool.Bash',
        start_time: now, status_code: 'PENDING',
        attributes: { command_preview: 'rm -rf build/', tool_use_id: toolUseId, is_test: true } },
      { trace_id: traceId, span_id: `permreq-${toolUseId}`, parent_id: null, name: 'permission.request',
        start_time: now, status_code: 'PENDING',
        attributes: { tool_name: 'Bash', tool_use_id: toolUseId, is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toBeVisible({ timeout: 10_000 })
    await expect(nowZone).toHaveAttribute('data-state', 'permission')
  })

  test('a PENDING AskUserQuestion shows the dedicated "question" state (v8)', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'now-zone question fixture', is_test: true } },
      { trace_id: traceId, span_id: `pending-tu-${sfx}`, parent_id: null,
        name: 'tool.AskUserQuestion', start_time: now, status_code: 'PENDING',
        attributes: { tool_name: 'AskUserQuestion', tool_use_id: `tu-${sfx}`, is_test: true,
          questions: [{ question: 'QUESTION_ONE_MARKER — which approach should we take?',
            header: 'Approach', multiSelect: false,
            options: [{ label: 'Option Alpha', description: 'first way' },
              { label: 'Option Beta', description: 'second way' },
              { label: 'Option Gamma', description: 'third way' }] }] } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toBeVisible({ timeout: 10_000 })
    // The question state — not the generic "running tool.AskUserQuestion" —
    // with the amber attention treatment, like permission.
    await expect(nowZone).toHaveAttribute('data-state', 'question')
    await expect(nowZone).toHaveClass(/live-now-attention/)
    await expect(nowZone).toContainText('waiting for your answer')
    await expect(nowZone).toContainText('QUESTION_ONE_MARKER')
    await expect(nowZone).toContainText('3 options')
    await expect(nowZone).not.toContainText('running tool')

    // options ▾ opens the read-only pending Q&A sheet (no chosen mark).
    await page.locator('[data-testid="live-now-options"]').click()
    const sheet = page.locator('[data-testid="live-sheet"]')
    await expect(sheet).toBeVisible()
    await expect(sheet).toContainText('Option Beta')
    await expect(sheet).toContainText('read-only')
    await expect(sheet.locator('.live-qa-chosen')).toHaveCount(0)
  })

  test('a second question cleanly replaces an answered first one (v8)', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    const q1 = 'FIRST_QUESTION_MARKER — where should the fix land?'
    const q2 = 'SECOND_QUESTION_MARKER — how should the NOW zone present a waiting question, including when another one arrives right after the first is answered?'
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'two-questions fixture', is_test: true } },
      // Question 1: already answered — its resolved span is in the tail.
      { trace_id: traceId, span_id: `ask1-${sfx}`, parent_id: null,
        name: 'tool.AskUserQuestion', start_time: now, duration_ms: 9000,
        attributes: { tool_name: 'AskUserQuestion', is_test: true,
          questions: [{ question: q1, header: 'Scope', multiSelect: false,
            options: [{ label: 'Artifact first', description: 'prototype now' },
              { label: 'Real page only', description: 'skip the prototype' }] }],
          answers: { [q1]: 'Artifact first' } } },
      // Question 2: waiting — a long question that fills both clamp lines.
      { trace_id: traceId, span_id: `pending-tu2-${sfx}`, parent_id: null,
        name: 'tool.AskUserQuestion', start_time: now, status_code: 'PENDING',
        attributes: { tool_name: 'AskUserQuestion', tool_use_id: `tu2-${sfx}`, is_test: true,
          questions: [{ question: q2, header: 'NOW zone', multiSelect: false,
            options: [{ label: 'Dedicated state' }, { label: 'Inline options' }] }] } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toBeVisible({ timeout: 10_000 })
    // The footer holds ONLY the newest unanswered question — no stale Q1 text.
    await expect(nowZone).toHaveAttribute('data-state', 'question')
    await expect(nowZone).toContainText('SECOND_QUESTION_MARKER')
    await expect(nowZone).not.toContainText('FIRST_QUESTION_MARKER')

    // options ▾ must stay visible below the clamp even for a long question.
    const btn = page.locator('[data-testid="live-now-options"]')
    await expect(btn).toBeVisible()
    const btnBox = await btn.boundingBox()
    const nowBox = await nowZone.boundingBox()
    expect(btnBox, 'options button must have a box').toBeTruthy()
    expect(btnBox.y + btnBox.height,
      'options ▾ must sit inside the footer, not clipped below the clamp')
      .toBeLessThanOrEqual(nowBox.y + nowBox.height + 1)

    // The answered question 1 landed in the tail as a delicate qa row.
    const qaRow = page.locator(`[data-testid="live-row"][data-span-id="ask1-${sfx}"]`)
    await expect(qaRow).toHaveAttribute('data-kind', 'qa')
    await expect(qaRow).toContainText('FIRST_QUESTION_MARKER')
    await expect(qaRow).toContainText('✓')
    await expect(qaRow).toContainText('Artifact first')

    // Tapping it opens the full Q&A sheet with the chosen option marked.
    await qaRow.click()
    const sheet = page.locator('[data-testid="live-sheet"]')
    await expect(sheet).toBeVisible()
    await expect(sheet.locator('.live-qa-chosen')).toHaveCount(1)
    await expect(sheet.locator('.live-qa-chosen')).toContainText('Artifact first')
  })

  test('a PENDING permission renders a delicate qa row in the tail (v8)', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'perm qa-row fixture', is_test: true } },
      { trace_id: traceId, span_id: `permreq-tu-${sfx}`, parent_id: null,
        name: 'permission.request', start_time: now, status_code: 'PENDING',
        attributes: { tool_name: 'Bash', tool_use_id: `tu-${sfx}`,
          command_preview: 'rm -rf web/static/dist', is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const qaRow = page.locator(`[data-testid="live-row"][data-span-id="permreq-tu-${sfx}"]`)
    await expect(qaRow).toBeVisible({ timeout: 10_000 })
    await expect(qaRow).toHaveAttribute('data-kind', 'qa')
    await expect(qaRow).toContainText('Permission')
    await expect(qaRow).toContainText('rm -rf web/static/dist')
    await expect(qaRow).toContainText('waiting for permission')
  })

  test('a promptlive- placeholder shows the "processing your prompt" state', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `promptlive-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, status_code: 'PENDING',
        attributes: { text: 'a queued prompt still processing', live_placeholder: true, is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toBeVisible({ timeout: 10_000 })
    await expect(nowZone).toHaveAttribute('data-state', 'prompt')
  })

  test('no PENDING spans: shows the latest assistant_response, clamped', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'now-zone response fixture', is_test: true } },
      { trace_id: traceId, span_id: `resp-${sfx}`, parent_id: null, name: 'assistant_response',
        start_time: now, attributes: { text: 'LATEST_RESPONSE_MARKER', is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toBeVisible({ timeout: 10_000 })
    await expect(nowZone).toHaveAttribute('data-state', 'response')
    await expect(nowZone).toContainText('LATEST_RESPONSE_MARKER')
  })

  test('a resolved PENDING span clears the zone within one poll (retired-prune)', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    const toolUseId = `tu-${sfx}`
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'now-zone resolve fixture', is_test: true } },
      { trace_id: traceId, span_id: `pending-${toolUseId}`, parent_id: null, name: 'tool.Bash',
        start_time: now, status_code: 'PENDING',
        attributes: { command_preview: 'npm test', tool_use_id: toolUseId, is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'tool', { timeout: 10_000 })

    // Resolve it: post the real tool.Bash result carrying the same
    // tool_use_id — the merge retires the `pending-<id>` placeholder.
    await post(page, [
      { trace_id: traceId, span_id: `bashresult-${sfx}`, parent_id: null, name: 'tool.Bash',
        start_time: now, end_time: new Date(Date.now() + 1000).toISOString(), status_code: 'OK',
        attributes: { command_preview: 'npm test', tool_use_id: toolUseId, is_test: true } },
      { trace_id: traceId, span_id: `resp-${sfx}`, parent_id: null, name: 'assistant_response',
        start_time: new Date(Date.now() + 1000).toISOString(),
        attributes: { text: 'RESOLVED_RESPONSE_MARKER', is_test: true } },
    ])

    // Within one ~4s poll the zone must flip off 'tool' — no stale spinner.
    await expect.poll(async () => nowZone.getAttribute('data-state'), { timeout: 10_000 })
      .not.toBe('tool')
  })
})

// ---- v5: NOW-zone elapsed rollover + idle/bridge composer -------------------
//
// The test server runs with agent_bridge DISABLED (default), so
// `bridge_reachable` is always false in real /map responses. Every
// bridge-dependent scenario patches the map response in-flight
// (route.fetch → patched json) and stubs the web-JWT proxy endpoint
// (`/api/sessions/<id>/bridge-send`) with page.route — the browser-side
// contract is what's under test, not tmux delivery.

// State is now a SERVER verdict (`phase` / `agent_phase`), not a client
// re-derivation — so a scenario that needs a specific state patches the map's
// phase directly instead of simulating quietness. `quietLastSeen` only keeps
// the CLIENT's stale/active gate (poll cadence) out of the way.
function quietLastSeen() {
  return new Date(Date.now() - 30_000).toISOString()
}

const PHASE_CONFIG = { working_window_sec: 12, idle_settle_sec: 6, inactive_threshold_sec: 600 }

// Build the phase fields for a map patch. `phase` sets both the rollup and
// agent_phase.main; pass `agentPhase` for a richer per-agent map.
function phaseFields(phase, agentPhase) {
  if (!phase && !agentPhase) return {}
  return {
    phase: phase || (agentPhase && agentPhase.main) || 'working',
    agent_phase: agentPhase || { main: phase },
    phase_config: PHASE_CONFIG,
  }
}

async function bridgeReachableMap(page, traceId, { pane = '%3', phase, agentPhase } = {}) {
  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    const resp = await route.fetch()
    const json = await resp.json()
    await route.fulfill({
      response: resp,
      json: {
        ...json,
        bridge_reachable: true,
        bridge_pane: pane,
        last_seen: quietLastSeen(),
        ...phaseFields(phase, agentPhase),
      },
    })
  })
}

// Stub the proxy; returns the collected POST bodies. `delayMs` keeps the
// "delivering…" phase observable.
async function stubBridgeSend(page, traceId, result, { delayMs = 0 } = {}) {
  const posts = []
  await page.route(`**/api/sessions/${traceId}/bridge-send`, async (route) => {
    posts.push(route.request().postDataJSON())
    if (delayMs) await new Promise((r) => setTimeout(r, delayMs))
    await route.fulfill({ json: { id: 1, ...result } })
  })
  return posts
}

// Stub the answer proxy; returns the collected POST bodies.
async function stubBridgeAnswer(page, traceId, result = { delivered: true, detail: 'ok' }) {
  const posts = []
  await page.route(`**/api/sessions/${traceId}/bridge-answer`, async (route) => {
    posts.push(route.request().postDataJSON())
    await route.fulfill({ json: { id: 1, ...result } })
  })
  return posts
}

async function postActiveSession(page, { pendingTool = false, extraRows = 0 } = {}) {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const now = new Date().toISOString()
  const spans = [
    { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
      start_time: now, attributes: { text: 'bridge fixture prompt', is_test: true } },
    { trace_id: traceId, span_id: `resp-${sfx}`, parent_id: null, name: 'assistant_response',
      start_time: now, attributes: { text: 'IDLE_RESPONSE_MARKER', is_test: true } },
  ]
  for (let i = 0; i < extraRows; i++) {
    spans.push({ trace_id: traceId, span_id: `read-${sfx}-${i}`, parent_id: null,
      name: 'tool.Read', start_time: now,
      attributes: { file_path: `src/f${i}.js`, is_test: true } })
  }
  if (pendingTool) {
    spans.push({ trace_id: traceId, span_id: `pending-tu-${sfx}`, parent_id: null,
      name: 'tool.Bash', start_time: now, status_code: 'PENDING',
      attributes: { command_preview: 'npx vite build', tool_use_id: `tu-${sfx}`, is_test: true } })
  }
  await post(page, spans)
  return { traceId, sfx }
}

const composer = (page) => page.locator('[data-testid="live-composer"]')
const composerTa = (page) => page.locator('[data-testid="live-composer-ta"]')
const composerSend = (page) => page.locator('[data-testid="live-composer-send"]')
const bridgeMeta = (page) => page.locator('[data-testid="live-bridge-meta"]')

test.describe('NOW-zone elapsed rollover (duration fix)', () => {
  test('a PENDING tool started 10 minutes ago reads rolled-over minutes on a fresh load, and keeps ticking', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const tenMinAgo = new Date(Date.now() - 600_000).toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: tenMinAgo, attributes: { text: 'rollover fixture', is_test: true } },
      { trace_id: traceId, span_id: `pending-tu-${sfx}`, parent_id: null, name: 'tool.Agent',
        start_time: tenMinAgo, status_code: 'PENDING',
        attributes: { tool_name: 'Agent', tool_use_id: `tu-${sfx}`, is_test: true } },
    ])
    // A genuinely long-running tool on an active session: force the working
    // phase (the 10-min-old span would otherwise age out of the working
    // window server-side and read idle). The rollover formatter is what's
    // under test here, not the phase.
    await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
      const resp = await route.fetch()
      const json = await resp.json()
      await route.fulfill({ response: resp, json: { ...json, ...phaseFields('working') } })
    })
    // The 10-min-old pending tool is reparented under the prompt anchor, so it
    // arrives via the deep-children fetch — where the server demotes it to a
    // resolved-interrupted rendering (_demote_stale_pending: inactive session +
    // pending older than 60s), stripping the very 'tool' state under test.
    // Re-assert its PENDING placeholder in the children response.
    const pendingSpanId = `pending-tu-${sfx}`
    await page.route(`**/api/sessions/${traceId}/spans/*/children*`, async (route) => {
      const resp = await route.fetch()
      const json = await resp.json()
      const spans = (json.spans || []).map((s) => (s.span_id === pendingSpanId
        ? { ...s, status_code: 'PENDING',
          attributes: { tool_name: 'Agent', tool_use_id: `tu-${sfx}`, is_test: true } }
        : s))
      await route.fulfill({ response: resp, json: { ...json, spans } })
    })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'tool', { timeout: 10_000 })

    // A fresh load shows the FULL elapsed (anchored to start_time), in the
    // rolled-over unit — "10m02s", never a raw seconds dump like "602s".
    const elapsed = nowZone.locator('.live-now-elapsed')
    await expect(elapsed).toHaveText(/^10m\d{2}s$/, { timeout: 5_000 })

    // Still ticks every second after the rollover.
    const t1 = await elapsed.textContent()
    await page.waitForTimeout(2_100)
    const t2 = await elapsed.textContent()
    expect(t2, 'rolled-over elapsed must keep ticking').not.toBe(t1)
    expect(t2).toMatch(/^10m\d{2}s$/)
  })
})

test.describe('Idle state + bridge composer (v5)', () => {
  test('alive + no pending + bridge reachable → idle: composer, steady dot, no caret', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    await bridgeReachableMap(page, traceId, { phase: 'idle' })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'idle', { timeout: 20_000 })

    // Full composer with the idle placeholder + bridge meta naming the pane.
    await expect(composerTa(page)).toBeVisible()
    await expect(composerTa(page)).toHaveAttribute('placeholder', /Send a prompt/)
    await expect(bridgeMeta(page)).toContainText('%3')
    await expect(bridgeMeta(page)).toContainText('starts the next turn')
    // Send disabled while the textarea is empty.
    await expect(composerSend(page)).toBeDisabled()

    // Header: steady green dot + "idle" label (pulse class absent).
    await expect(page.locator('[data-testid="live-header"]')).toContainText('idle')
    const dot = page.locator('.live-status-dot')
    await expect(dot).toHaveClass(/live-status-idle/)
    await expect(dot).not.toHaveClass(/live-status-running/)

    // Idle suppresses the caret on the last tail row.
    await expect(page.locator('.live-caret')).toHaveCount(0)
  })

  test('bridge not reachable → still idle (server phase), but no composer', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    // Patch the phase to idle; leave the bridge unreachable (real server
    // default — disabled). Idle is a SERVER verdict, independent of the
    // bridge; the bridge only gates the composer's visibility.
    await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
      const resp = await route.fetch()
      const json = await resp.json()
      await route.fulfill({ response: resp, json: { ...json, ...phaseFields('idle') } })
    })
    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'idle', { timeout: 10_000 })
    await expect(composer(page)).toHaveCount(0)
  })

  test('question / permission / finished never render a composer, even when reachable', async ({ page }) => {
    const now = new Date().toISOString()
    const fixtures = {
      question: (traceId, sfx) => [
        { trace_id: traceId, span_id: `pending-tu-${sfx}`, parent_id: null,
          name: 'tool.AskUserQuestion', start_time: now, status_code: 'PENDING',
          attributes: { tool_name: 'AskUserQuestion', tool_use_id: `tu-${sfx}`, is_test: true,
            questions: [{ question: 'Which way?', options: [{ label: 'A' }, { label: 'B' }] }] } },
      ],
      permission: (traceId, sfx) => [
        { trace_id: traceId, span_id: `permreq-tu-${sfx}`, parent_id: null,
          name: 'permission.request', start_time: now, status_code: 'PENDING',
          attributes: { tool_name: 'Bash', tool_use_id: `tu-${sfx}`, is_test: true } },
      ],
      finished: (traceId, sfx) => [
        { trace_id: traceId, span_id: `end-${sfx}`, parent_id: null, name: 'session.end',
          start_time: now, status_code: 'OK', attributes: { reason: 'clear', is_test: true } },
      ],
    }
    for (const [state, extra] of Object.entries(fixtures)) {
      const traceId = randomUUID()
      const sfx = traceId.slice(0, 8)
      await post(page, [
        { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
          start_time: now, attributes: { text: `${state} composer-free fixture`, is_test: true } },
        ...extra(traceId, sfx),
      ])
      await bridgeReachableMap(page, traceId)
      await page.goto(`/live/${traceId}`)
      await settle(page)
      const nowZone = page.locator('[data-testid="live-now"]')
      await expect(nowZone).toHaveAttribute('data-state', state, { timeout: 10_000 })
      await expect(composer(page), `${state} must not render a composer`).toHaveCount(0)
    }
  })

  test('a mid-draft composer unmount (one-poll reachability blip) preserves the draft', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    // Serve bridge_reachable=true except on the FOURTH map response — a
    // one-poll blip (tmux hiccup / registry churn) that unmounts the composer
    // and must not eat the user's typed draft. The phase stays idle
    // throughout (a server verdict, NOT bridge-gated) — only the composer's
    // visibility follows the bridge.
    let served = 0
    await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
      const resp = await route.fetch()
      const json = await resp.json()
      served += 1
      const reachable = served !== 4
      await route.fulfill({
        response: resp,
        json: {
          ...json,
          bridge_reachable: reachable,
          bridge_pane: reachable ? '%3' : null,
          last_seen: quietLastSeen(),
          ...phaseFields('idle'),
        },
      })
    })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'idle', { timeout: 20_000 })
    await expect(composerTa(page)).toBeVisible()

    await composerTa(page).fill('half-typed steering thought')

    // Blip: the served===4 poll reports unreachable → the composer unmounts,
    // but the state stays idle (phase is a server verdict, not bridge-gated).
    await expect(composer(page)).toHaveCount(0, { timeout: 20_000 })
    await expect(nowZone).toHaveAttribute('data-state', 'idle')

    // Recovery: the next reachable poll remounts the composer with the draft.
    await expect(composerTa(page)).toBeVisible({ timeout: 20_000 })
    await expect(composerTa(page)).toHaveValue('half-typed steering thought')
  })

  test('response and tool states show the compact steer composer', async ({ page }) => {
    // response state
    const idle = await postActiveSession(page)
    await bridgeReachableMap(page, idle.traceId)
    // A pending tool + working phase forces 'tool' — bridge reachable → steer.
    const busy = await postActiveSession(page, { pendingTool: true })
    await bridgeReachableMap(page, busy.traceId, { phase: 'working' })

    await page.goto(`/live/${busy.traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'tool', { timeout: 10_000 })
    await expect(composer(page)).toHaveClass(/live-composer-steer/)
    await expect(composerTa(page)).toHaveAttribute('placeholder', /Steer the agent/)
    await expect(bridgeMeta(page)).toContainText('queues into the running turn')
  })

  // Bug B: the send affordance is gated on bridgeReachable (a live tmux
  // pane), NEVER on the staleness verdict — delivery works fine on an
  // inactive-but-bridged session. Staleness may only change copy, never
  // whether the composer exists.
  test('inactive-stale + bridge reachable → the composer still renders (staleness is copy-only)', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    await bridgeReachableMap(page, traceId, { phase: 'inactive-stale' })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'inactive', { timeout: 20_000 })
    await expect(page.locator('[data-testid="live-header"]')).toContainText('inactive')
    await expect(composerTa(page)).toBeVisible()
    await expect(composerSend(page)).toBeDisabled()
  })

  test('inactive-stale + bridge NOT reachable → no composer', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
      const resp = await route.fetch()
      const json = await resp.json()
      await route.fulfill({ response: resp, json: { ...json, ...phaseFields('inactive-stale') } })
    })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'inactive', { timeout: 20_000 })
    await expect(composer(page)).toHaveCount(0)
  })
})

// Stub the read-only screen-peek proxy; returns the collected request count.
async function stubBridgeScreen(page, traceId, result) {
  let calls = 0
  await page.route(`**/api/sessions/${traceId}/bridge-screen*`, async (route) => {
    calls++
    await route.fulfill({ json: result })
  })
  return () => calls
}

test.describe('Terminal peek sheet', () => {
  test('bridge not reachable → no terminal button in the header', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-terminal-btn"]')).toHaveCount(0)
  })

  test('bridge reachable → button opens a one-shot snapshot with pane label', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    await bridgeReachableMap(page, traceId, { pane: '%9' })
    const callCount = await stubBridgeScreen(page, traceId,
      { ok: true, html: '<span style="color:#87d787">hello</span>', detail: 'captured %9' })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await page.locator('[data-testid="live-terminal-btn"]').click()

    const sheet = page.locator('[data-testid="live-terminal-sheet"]')
    await expect(sheet).toBeVisible()
    await expect(page.locator('[data-testid="live-terminal-body"]')).toContainText('hello')
    await expect(sheet).toContainText('%9')
    await expect(sheet).toContainText('one-shot snapshot')
    expect(callCount()).toBe(1)

    // Manual refresh re-fetches — no auto-polling.
    await page.locator('[data-testid="live-terminal-refresh"]').click()
    await expect.poll(callCount).toBe(2)
  })

  test('capture refusal renders the detail, not a blank/broken pane', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    await bridgeReachableMap(page, traceId)
    await stubBridgeScreen(page, traceId, { ok: false, html: '', detail: 'no reachable session' })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await page.locator('[data-testid="live-terminal-btn"]').click()
    await expect(page.locator('[data-testid="live-terminal-error"]'))
      .toContainText('no reachable session')
    await expect(page.locator('[data-testid="live-terminal-body"]')).toHaveCount(0)
  })

  test('overflowing content opens scrolled to the BOTTOM — the live status line', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    await bridgeReachableMap(page, traceId)
    const lines = Array.from({ length: 80 }, (_, i) => `line ${i}`)
    lines.push('-- INSERT -- ⏵⏵ bypass permissions on')
    await stubBridgeScreen(page, traceId,
      { ok: true, html: lines.join('\n'), detail: 'captured %3' })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await page.locator('[data-testid="live-terminal-btn"]').click()

    const body = page.locator('[data-testid="live-terminal-body"]')
    await expect(body).toContainText('-- INSERT --')
    // The bottom line (the live status) must be scrolled into view without
    // any manual scrolling — never left stranded below the fold.
    await expect(body.locator('text=-- INSERT --')).toBeInViewport()
    const [scrollTop, maxScroll] = await body.evaluate(
      (el) => [el.scrollTop, el.scrollHeight - el.clientHeight])
    expect(maxScroll).toBeGreaterThan(0) // sanity: content actually overflows
    expect(scrollTop).toBeGreaterThanOrEqual(maxScroll - 1) // opened at the bottom
  })
})

test.describe('Bridge send lifecycle (v5)', () => {
  test('delivered path: delivering → ✓ detail, textarea clears + re-enables, state and rows unchanged', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    await bridgeReachableMap(page, traceId, { phase: 'idle' })
    const posts = await stubBridgeSend(page, traceId,
      { delivered: true, detail: 'delivered to %3' }, { delayMs: 400 })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'idle', { timeout: 20_000 })
    const rowsBefore = await rows(page).count()

    await composerTa(page).fill('run the flaky spec again')
    await expect(composerSend(page)).toBeEnabled()
    await composerSend(page).click()

    // Delivering phase: spinner meta + disabled textarea.
    await expect(bridgeMeta(page)).toContainText('delivering', { timeout: 2_000 })
    await expect(composerTa(page)).toBeDisabled()

    // Delivered: server detail surfaced, composer freed and cleared.
    await expect(bridgeMeta(page)).toContainText('✓ delivered to %3', { timeout: 5_000 })
    await expect(composerTa(page)).toBeEnabled()
    await expect(composerTa(page)).toHaveValue('')
    expect(posts).toEqual([{ text: 'run the flaky spec again' }])

    // Sending never changes data-state, and NO client-stamped row appears —
    // the prompt lands only when the poll returns the real span (the stubbed
    // map never will, so the count must hold).
    await expect(nowZone).toHaveAttribute('data-state', 'idle')
    expect(await rows(page).count(), 'send must not append a client-stamped row').toBe(rowsBefore)
  })

  test('failure path: {delivered:false} surfaces detail, preserves the text, re-enables', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    await bridgeReachableMap(page, traceId, { phase: 'idle' })
    await stubBridgeSend(page, traceId,
      { delivered: false, detail: 'no reachable session' })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-now"]'))
      .toHaveAttribute('data-state', 'idle', { timeout: 20_000 })

    await composerTa(page).fill('keep this draft')
    await composerSend(page).click()

    await expect(bridgeMeta(page)).toContainText('no reachable session', { timeout: 5_000 })
    await expect(composerTa(page)).toBeEnabled()
    await expect(composerTa(page)).toHaveValue('keep this draft')
  })

  test('steer send from a working state keeps the state; Cmd/Ctrl+Enter sends', async ({ page }) => {
    const { traceId } = await postActiveSession(page, { pendingTool: true })
    await bridgeReachableMap(page, traceId, { phase: 'working' })
    const posts = await stubBridgeSend(page, traceId,
      { delivered: true, detail: 'delivered to %3' })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'tool', { timeout: 10_000 })

    await composerTa(page).fill('also check the dark theme')
    await composerTa(page).press('ControlOrMeta+Enter')

    await expect(bridgeMeta(page)).toContainText('✓ delivered to %3', { timeout: 5_000 })
    expect(posts).toEqual([{ text: 'also check the dark theme' }])
    await expect(nowZone).toHaveAttribute('data-state', 'tool')
  })
})

test.describe('Composer pinning + geometry (v5)', () => {
  test('pinned tail stays pinned across composer appearance and autogrow; nothing clips', async ({ page }) => {
    const { traceId } = await postActiveSession(page, { extraRows: 30 })
    await bridgeReachableMap(page, traceId, { phase: 'idle' })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'idle', { timeout: 20_000 })

    const tail = page.locator('[data-testid="live-tail"]')
    const gap = () => tail.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight)

    // Pinned after boot + composer mount (the zone grew past the old cap).
    await page.waitForTimeout(300)
    expect(await gap(), 'tail must stay pinned when the composer mounts').toBeLessThan(40)

    // Autogrow: multi-line draft grows the zone; the tail must re-pin.
    await composerTa(page).fill('line one\nline two\nline three\nline four\nline five')
    await page.waitForTimeout(300)
    expect(await gap(), 'tail must stay pinned across textarea autogrow').toBeLessThan(40)

    // Nothing clips under the old 104px cap: the send button renders fully
    // inside the (now taller) zone.
    const nowBox = await nowZone.boundingBox()
    const sendBox = await composerSend(page).boundingBox()
    expect(sendBox, 'send button must be visible (not clipped)').toBeTruthy()
    expect(sendBox.y + sendBox.height).toBeLessThanOrEqual(nowBox.y + nowBox.height + 1)
    expect(nowBox.height, 'idle zone must exceed the old 104px cap').toBeGreaterThan(104)
  })

  test('the "N new" chip rides above the taller composer zone', async ({ page }) => {
    const { traceId, sfx } = await postActiveSession(page, { extraRows: 25 })
    await bridgeReachableMap(page, traceId, { phase: 'idle' })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'idle', { timeout: 20_000 })

    const tail = page.locator('[data-testid="live-tail"]')
    await tail.evaluate((el) => { el.scrollTop = 0 })
    await page.waitForTimeout(150)

    await post(page, [
      { trace_id: traceId, span_id: `newspan-${sfx}`, parent_id: null, name: 'tool.Write',
        start_time: new Date(Date.now() + 1000).toISOString(),
        attributes: { file_path: 'src/late-arrival.js', is_test: true } },
    ])

    const chip = page.locator('[data-testid="live-newchip"]')
    await expect(chip).toBeVisible({ timeout: 8_000 })
    const chipBox = await chip.boundingBox()
    const nowBox = await nowZone.boundingBox()
    expect(chipBox.y + chipBox.height,
      'chip must sit above the composer zone, not under it')
      .toBeLessThanOrEqual(nowBox.y + 1)
  })
})

// ---- v6: slash-command / skill autocomplete in the composer -----------------
//
// The composer's `/`-autocomplete. The catalog endpoint
// (`/api/sessions/<id>/bridge-commands`) is stubbed with a deterministic
// two-item fixture so ordering/filtering is exact; `bridgeReachableMap` forces
// the idle composer to render. Menu contract: data-testids live-command-menu,
// live-command-item, live-command-empty; the accept writes `/<name> `.
async function stubBridgeCommands(page, traceId, commands) {
  await page.route(`**/api/sessions/${traceId}/bridge-commands`, async (route) => {
    await route.fulfill({ json: { commands } })
  })
}

const FIXTURE_COMMANDS = [
  { name: 'deploy', description: 'Ship the current branch to prod.', kind: 'command', scope: 'project' },
  { name: 'lint', description: 'Lint the tree.', kind: 'skill', scope: 'project' },
]

const cmdMenu = (page) => page.locator('[data-testid="live-command-menu"]')
const cmdItems = (page) => page.locator('[data-testid="live-command-item"]')

async function idleComposer(page, traceId) {
  await bridgeReachableMap(page, traceId, { phase: 'idle' })
  await stubBridgeCommands(page, traceId, FIXTURE_COMMANDS)
  await page.goto(`/live/${traceId}`)
  await settle(page)
  await expect(page.locator('[data-testid="live-now"]'))
    .toHaveAttribute('data-state', 'idle', { timeout: 20_000 })
}

test.describe('Slash-command autocomplete (v6)', () => {
  test('typing "/" opens the menu with every command + its description and kind', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    await idleComposer(page, traceId)

    await composerTa(page).fill('/')
    await expect(cmdMenu(page)).toBeVisible({ timeout: 5_000 })
    await expect(cmdItems(page)).toHaveCount(2)
    await expect(cmdItems(page).nth(0)).toContainText('/deploy')
    await expect(cmdItems(page).nth(0)).toContainText('Ship the current branch')
    await expect(cmdItems(page).nth(0)).toContainText('command')
    await expect(cmdItems(page).nth(1)).toContainText('/lint')
    await expect(cmdItems(page).nth(1)).toContainText('skill')
  })

  test('the query narrows the list; a non-match shows the empty state', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    await idleComposer(page, traceId)

    await composerTa(page).fill('/li')
    await expect(cmdItems(page)).toHaveCount(1)
    await expect(cmdItems(page).nth(0)).toContainText('/lint')

    await composerTa(page).fill('/zzz')
    await expect(page.locator('[data-testid="live-command-empty"]')).toBeVisible()
    await expect(cmdItems(page)).toHaveCount(0)
  })

  test('ArrowDown + Enter accepts the highlighted item as "/<name> " and closes', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    await idleComposer(page, traceId)

    await composerTa(page).fill('/')
    await expect(cmdMenu(page)).toBeVisible()
    await composerTa(page).press('ArrowDown') // highlight lint (index 1)
    await composerTa(page).press('Enter')

    await expect(composerTa(page)).toHaveValue('/lint ')
    await expect(cmdMenu(page)).toHaveCount(0) // caret past the token → closed
  })

  test('clicking an item inserts it; plain Enter (menu closed) does not send', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    const posts = await stubBridgeSend(page, traceId, { delivered: true, detail: 'ok' })
    await idleComposer(page, traceId)

    await composerTa(page).fill('/')
    await cmdItems(page).nth(0).click() // /deploy
    await expect(composerTa(page)).toHaveValue('/deploy ')

    // Menu is closed now; a plain Enter must NOT send (it inserts a newline).
    await composerTa(page).press('Enter')
    await expect(cmdMenu(page)).toHaveCount(0)
    expect(posts, 'plain Enter must never deliver').toEqual([])
  })

  test('after accepting, Cmd/Ctrl+Enter still sends the full prompt', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    const posts = await stubBridgeSend(page, traceId, { delivered: true, detail: 'delivered to %3' })
    await idleComposer(page, traceId)

    await composerTa(page).fill('/')
    await composerTa(page).press('Enter') // accept /deploy
    await composerTa(page).type('the web app')
    await composerTa(page).press('ControlOrMeta+Enter')

    await expect(bridgeMeta(page)).toContainText('✓ delivered to %3', { timeout: 5_000 })
    expect(posts).toEqual([{ text: '/deploy the web app' }])
  })

  test('Escape dismisses the menu without altering the draft', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    await idleComposer(page, traceId)

    await composerTa(page).fill('/dep')
    await expect(cmdMenu(page)).toBeVisible()
    await composerTa(page).press('Escape')
    await expect(cmdMenu(page)).toHaveCount(0)
    await expect(composerTa(page)).toHaveValue('/dep')
  })
})

test.describe('Long-content invariant (acceptance #8b)', () => {
  test('an 8KB response keeps the NOW zone capped, the tail majority-height, and the sheet 80dvh-capped', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    const HUGE = 'x'.repeat(8 * 1024)
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'long-content fixture', is_test: true } },
      { trace_id: traceId, span_id: `resp-${sfx}`, parent_id: null, name: 'assistant_response',
        start_time: now, attributes: { text: `${HUGE} TAIL_8K_MARKER`, is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)

    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toBeVisible({ timeout: 10_000 })
    const nowBox = await nowZone.boundingBox()
    expect(nowBox.height, `NOW zone grew to ${nowBox.height}px on an 8KB response`).toBeLessThanOrEqual(100)

    // The tail keeps >=50% of the 667px viewport's usable height.
    const tailBox = await page.locator('[data-testid="live-tail"]').boundingBox()
    expect(tailBox.height).toBeGreaterThanOrEqual(667 * 0.5)

    // "[more]" opens the full render in a sheet capped at 80dvh. No testid is
    // given for this control in the DOM contract, so it's targeted by its
    // documented label ("[more ▾]" in the design doc's layout mock) — this
    // selector may need adjusting once the real markup lands.
    await page.getByRole('button', { name: /more/i }).click()
    const sheet = page.locator('[data-testid="live-sheet"]')
    await expect(sheet).toBeVisible({ timeout: 5_000 })
    await expect(sheet).toContainText('TAIL_8K_MARKER')
    const sheetBox = await sheet.boundingBox()
    const capPx = 0.8 * 667
    expect(sheetBox.height, `sheet height ${sheetBox.height}px exceeds the 80dvh cap (~${capPx}px)`)
      .toBeLessThanOrEqual(capPx + 4)
  })
})

// ---- answer a pending single-question ask from the QA sheet -----------------
//
// The QA sheet is read-only by default. When the span is a PENDING
// single-question ask AND the bridge is reachable, it delegates to LiveQaAnswer:
// picking an option / typing your own / "chat about this" all STAGE first and
// only reach the pane on an explicit Confirm (select→confirm gate, so a mis-tap
// can't answer the live agent). A note on a pick is delivered as free text
// (`label — note`) since the terminal has no note field. These tests pin the
// browser-side contract (POST bodies, staging, confirm) — not tmux.
test.describe('Answer a pending ask from the QA sheet', () => {
  async function seedPendingQuestion(page, { options } = {}) {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'answerable-question fixture', is_test: true } },
      { trace_id: traceId, span_id: `pending-tu-${sfx}`, parent_id: null,
        name: 'tool.AskUserQuestion', start_time: now, status_code: 'PENDING',
        attributes: { tool_name: 'AskUserQuestion', tool_use_id: `tu-${sfx}`, is_test: true,
          questions: [{ question: 'ANSWERABLE_MARKER — which database?', header: 'DB',
            multiSelect: false,
            options: options || [{ label: 'Postgres', description: 'relational' },
              { label: 'SQLite', description: 'embedded' }] }] } },
    ])
    await bridgeReachableMap(page, traceId)
    return { traceId, sfx }
  }

  async function openAnswerer(page, traceId) {
    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'question', { timeout: 10_000 })
    await page.locator('[data-testid="live-now-options"]').click()
    const sheet = page.locator('[data-testid="live-sheet"]')
    await expect(sheet).toBeVisible()
    return sheet
  }

  test('selecting an option STAGES it and POSTs nothing until Confirm', async ({ page }) => {
    const { traceId } = await seedPendingQuestion(page)
    const posts = await stubBridgeAnswer(page, traceId, { delivered: true, detail: 'selected option 2 in %3' })

    const sheet = await openAnswerer(page, traceId)
    const picks = sheet.locator('[data-testid="live-qa-pick"]')
    await expect(picks).toHaveCount(2)

    // Tap the second option → staged, NOT sent.
    await picks.nth(1).click()
    const confirm = sheet.locator('[data-testid="live-qa-confirm"]')
    await expect(confirm).toBeVisible()
    await expect(confirm).toContainText('SQLite')
    await page.waitForTimeout(200)
    expect(posts.length).toBe(0)  // nothing reached the pane on select

    // Confirm → exactly one POST {option_index:1, label:'SQLite'}, no text.
    await sheet.locator('[data-testid="live-qa-confirm-send"]').click()
    await expect.poll(() => posts.length).toBe(1)
    expect(posts[0].option_index).toBe(1)
    expect(posts[0].label).toBe('SQLite')
    expect(posts[0].text).toBeUndefined()
    expect(posts[0].chat).toBeUndefined()
    await expect(sheet).toBeHidden({ timeout: 5_000 })
  })

  test('Cancel discards the staged option and POSTs nothing', async ({ page }) => {
    const { traceId } = await seedPendingQuestion(page)
    const posts = await stubBridgeAnswer(page, traceId)

    const sheet = await openAnswerer(page, traceId)
    await sheet.locator('[data-testid="live-qa-pick"]').first().click()
    await expect(sheet.locator('[data-testid="live-qa-confirm"]')).toBeVisible()
    await sheet.locator('[data-testid="live-qa-cancel"]').click()
    await expect(sheet.locator('[data-testid="live-qa-confirm"]')).toBeHidden()
    await page.waitForTimeout(200)
    expect(posts.length).toBe(0)
    await expect(sheet).toBeVisible()  // stays open to pick again
  })

  test('a note on a pick is delivered as free text at the "Type something." index', async ({ page }) => {
    const { traceId } = await seedPendingQuestion(page)
    const posts = await stubBridgeAnswer(page, traceId, { delivered: true, detail: 'typed answer delivered to %3' })

    const sheet = await openAnswerer(page, traceId)
    await sheet.locator('[data-testid="live-qa-pick"]').first().click()  // Postgres
    await sheet.locator('[data-testid="live-qa-note-input"]').fill('prod-grade')
    await sheet.locator('[data-testid="live-qa-confirm-send"]').click()

    await expect.poll(() => posts.length).toBe(1)
    // Two options → free-text entry is index 2; the note rides as label — note.
    expect(posts[0].option_index).toBe(2)
    expect(posts[0].text).toBe('Postgres — prod-grade')
    expect(posts[0].label).toBe('Postgres')
    await expect(sheet).toBeHidden({ timeout: 5_000 })
  })

  test('"Type your own" answers the "Type something." entry (index = option count)', async ({ page }) => {
    const { traceId } = await seedPendingQuestion(page)
    const posts = await stubBridgeAnswer(page, traceId, { delivered: true, detail: 'typed answer delivered to %3' })

    const sheet = await openAnswerer(page, traceId)
    await sheet.locator('[data-testid="live-qa-stage-free"]').click()
    await sheet.locator('[data-testid="live-qa-free-input"]').fill('MySQL, actually')
    await sheet.locator('[data-testid="live-qa-confirm-send"]').click()

    await expect.poll(() => posts.length).toBe(1)
    expect(posts[0].option_index).toBe(2)
    expect(posts[0].text).toBe('MySQL, actually')
    await expect(sheet).toBeHidden({ timeout: 5_000 })
  })

  test('"Chat about this" POSTs the chat entry (index = options+1) with chat:true', async ({ page }) => {
    const { traceId } = await seedPendingQuestion(page)
    const posts = await stubBridgeAnswer(page, traceId, { delivered: true, detail: 'selected option 4 in %3' })

    const sheet = await openAnswerer(page, traceId)
    await sheet.locator('[data-testid="live-qa-stage-chat"]').click()
    await sheet.locator('[data-testid="live-qa-chat-input"]').fill('why these three?')
    await sheet.locator('[data-testid="live-qa-confirm-send"]').click()

    await expect.poll(() => posts.length).toBe(1)
    expect(posts[0].option_index).toBe(3)  // 2 options → chat entry at index 3
    expect(posts[0].chat).toBe(true)
    expect(posts[0].text).toBe('why these three?')
    expect(posts[0].label).toBe('Chat about this')
    await expect(sheet).toBeHidden({ timeout: 5_000 })
  })

  test('a failed answer surfaces the detail and keeps the sheet open', async ({ page }) => {
    const { traceId } = await seedPendingQuestion(page)
    await stubBridgeAnswer(page, traceId, { delivered: false, detail: 'no reachable session' })

    const sheet = await openAnswerer(page, traceId)
    await sheet.locator('[data-testid="live-qa-pick"]').first().click()
    await sheet.locator('[data-testid="live-qa-confirm-send"]').click()
    await expect(sheet).toContainText('no reachable session')
    await expect(sheet).toBeVisible()  // not closed on failure
  })

  test('an option preview renders as a disclosure in the answerer', async ({ page }) => {
    const { traceId } = await seedPendingQuestion(page, {
      options: [{ label: 'Postgres', description: 'relational', preview: 'CREATE TABLE t (id int);' },
        { label: 'SQLite', description: 'embedded' }],
    })
    await stubBridgeAnswer(page, traceId)

    const sheet = await openAnswerer(page, traceId)
    const preview = sheet.locator('[data-testid="live-qa-preview"]')
    await expect(preview).toHaveCount(1)  // only the option that has one
    await preview.locator('summary').click()
    await expect(preview).toContainText('CREATE TABLE t')
  })
})

// ---- answer a pending MULTI-question ask from the QA sheet ------------------
//
// When every question is single-select, a many-question ask is answerable as a
// Prev/Next stepper: the operator answers each question (changing any freely),
// and NOTHING reaches the pane until "Send all", which delivers the answers in
// order — each POST carrying `confirm_text` (that question) so the backend can
// refuse a stale focus. An ask with a multi-select question stays read-only.
test.describe('Answer a pending multi-question ask', () => {
  async function seedMultiQuestion(page, { firstMultiSelect = false } = {}) {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'multi-question fixture', is_test: true } },
      { trace_id: traceId, span_id: `pending-tu-${sfx}`, parent_id: null,
        name: 'tool.AskUserQuestion', start_time: now, status_code: 'PENDING',
        attributes: { tool_name: 'AskUserQuestion', tool_use_id: `tu-${sfx}`, is_test: true,
          questions: [
            { question: 'Q1_MARKER — which box zooms?', header: 'Box',
              multiSelect: firstMultiSelect,
              options: [{ label: 'Composer' }, { label: 'Search' }] },
            { question: 'Q2_MARKER — which cache?', header: 'Cache', multiSelect: false,
              options: [{ label: 'Bust' }, { label: 'Keep' }, { label: 'Reset' }] },
          ] } },
    ])
    await bridgeReachableMap(page, traceId)
    return { traceId, sfx }
  }

  async function openMulti(page, traceId) {
    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'question', { timeout: 10_000 })
    await page.locator('[data-testid="live-now-options"]').click()
    const sheet = page.locator('[data-testid="live-sheet"]')
    await expect(sheet).toBeVisible()
    return sheet
  }

  test('one atomic POST carries every answer in order with confirm_text', async ({ page }) => {
    const { traceId } = await seedMultiQuestion(page)
    const posts = await stubBridgeAnswer(page, traceId, { delivered: true, detail: 'submitted 2 answers' })
    const sheet = await openMulti(page, traceId)

    await expect(sheet.locator('[data-testid="live-qa-answer-multi"]')).toBeVisible()
    await expect(sheet.locator('[data-testid="live-qa-step"]')).toContainText('Question 1 of 2')
    const sendAll = sheet.locator('[data-testid="live-qa-send-all"]')
    await expect(sendAll).toBeDisabled()  // no send until every question answered

    // Answer Q1 (Search), advance to Q2.
    await sheet.locator('[data-testid="live-qa-pick"]').nth(1).click()
    await sheet.locator('[data-testid="live-qa-next"]').click()
    await expect(sheet.locator('[data-testid="live-qa-step"]')).toContainText('Question 2 of 2')

    // Answer Q2 (Reset) — now every question is answered.
    await sheet.locator('[data-testid="live-qa-pick"]').nth(2).click()
    await expect(sendAll).toBeEnabled()

    await page.waitForTimeout(150)
    expect(posts.length).toBe(0)  // nothing reached the pane before Send-all

    await sendAll.click()
    await expect.poll(() => posts.length).toBe(1)  // ONE atomic request
    const { answers } = posts[0]
    expect(answers).toHaveLength(2)
    expect(answers[0].option_index).toBe(1)
    expect(answers[0].label).toBe('Search')
    expect(answers[0].confirm_text).toContain('Q1_MARKER')
    expect(answers[1].option_index).toBe(2)
    expect(answers[1].label).toBe('Reset')
    expect(answers[1].confirm_text).toContain('Q2_MARKER')
    await expect(sheet).toBeHidden({ timeout: 5_000 })
  })

  test('Previous lets you change an earlier answer before sending', async ({ page }) => {
    const { traceId } = await seedMultiQuestion(page)
    const posts = await stubBridgeAnswer(page, traceId, { delivered: true, detail: 'submitted' })
    const sheet = await openMulti(page, traceId)

    await sheet.locator('[data-testid="live-qa-pick"]').nth(0).click()  // Q1 Composer
    await sheet.locator('[data-testid="live-qa-next"]').click()
    await sheet.locator('[data-testid="live-qa-pick"]').nth(0).click()  // Q2 Bust
    // Switch back to Q1 and choose differently.
    await sheet.locator('[data-testid="live-qa-prev"]').click()
    await expect(sheet.locator('[data-testid="live-qa-step"]')).toContainText('Question 1 of 2')
    await sheet.locator('[data-testid="live-qa-pick"]').nth(1).click()  // Q1 Search now
    await sheet.locator('[data-testid="live-qa-send-all"]').click()

    await expect.poll(() => posts.length).toBe(1)
    expect(posts[0].answers[0].label).toBe('Search')  // the revised choice, not Composer
    expect(posts[0].answers[1].label).toBe('Bust')
  })

  test('a backend refusal surfaces the detail and keeps the sheet open', async ({ page }) => {
    const { traceId } = await seedMultiQuestion(page)
    // The atomic walk refused mid-way (a focus-guard failure names the question).
    await stubBridgeAnswer(page, traceId,
      { delivered: false, detail: 'question 2 not focused in pane' })
    const sheet = await openMulti(page, traceId)
    await sheet.locator('[data-testid="live-qa-pick"]').nth(0).click()
    await sheet.locator('[data-testid="live-qa-next"]').click()
    await sheet.locator('[data-testid="live-qa-pick"]').nth(0).click()
    await sheet.locator('[data-testid="live-qa-send-all"]').click()

    await expect(sheet).toContainText('question 2 not focused')
    await expect(sheet).toBeVisible()  // not closed on failure
  })

  test('Type-your-own in a question rides the free-text entry in the atomic POST', async ({ page }) => {
    const { traceId } = await seedMultiQuestion(page)
    const posts = await stubBridgeAnswer(page, traceId, { delivered: true, detail: 'submitted' })
    const sheet = await openMulti(page, traceId)

    // Q1: type your own instead of picking a listed option.
    await sheet.locator('[data-testid="live-qa-stage-free"]').click()
    await sheet.locator('[data-testid="live-qa-free-input"]').fill('a custom box')
    await sheet.locator('[data-testid="live-qa-next"]').click()
    // Q2: pick Keep (index 1).
    await sheet.locator('[data-testid="live-qa-pick"]').nth(1).click()
    await sheet.locator('[data-testid="live-qa-send-all"]').click()

    await expect.poll(() => posts.length).toBe(1)
    const { answers } = posts[0]
    // Q1 free-text → option_index = the option count (2), text carries the answer.
    expect(answers[0].option_index).toBe(2)
    expect(answers[0].text).toBe('a custom box')
    expect(answers[1].option_index).toBe(1)
    expect(answers[1].label).toBe('Keep')
  })

  test('an ask with a multi-select question stays read-only with a reason', async ({ page }) => {
    const { traceId } = await seedMultiQuestion(page, { firstMultiSelect: true })
    const sheet = await openMulti(page, traceId)
    await expect(sheet.locator('[data-testid="live-qa-answer-multi"]')).toHaveCount(0)
    await expect(sheet.locator('[data-testid="live-qa-answer"]')).toHaveCount(0)
    await expect(sheet).toContainText('multi-select')
  })

  test('tapping an option after typing keeps the draft; switching back restores and sends it', async ({ page }) => {
    const { traceId } = await seedMultiQuestion(page)
    const posts = await stubBridgeAnswer(page, traceId, { delivered: true, detail: 'submitted' })
    const sheet = await openMulti(page, traceId)

    // Q1: type a custom answer, then tap a listed option (the incident: this
    // used to silently wipe the draft and deliver the option instead).
    await sheet.locator('[data-testid="live-qa-stage-free"]').click()
    await sheet.locator('[data-testid="live-qa-free-input"]').fill('my own answer')
    await sheet.locator('[data-testid="live-qa-pick"]').nth(1).click()
    // The draft survives the toggle and is visible on the Type-your-own row.
    await expect(sheet.locator('[data-testid="live-qa-stage-free"]')).toContainText('my own answer')
    // Switch back — the field restores the typed draft.
    await sheet.locator('[data-testid="live-qa-stage-free"]').click()
    await expect(sheet.locator('[data-testid="live-qa-free-input"]')).toHaveValue('my own answer')

    await sheet.locator('[data-testid="live-qa-next"]').click()
    await sheet.locator('[data-testid="live-qa-pick"]').nth(1).click()  // Q2 Keep
    await sheet.locator('[data-testid="live-qa-send-all"]').click()

    await expect.poll(() => posts.length).toBe(1)
    const { answers } = posts[0]
    expect(answers[0].option_index).toBe(2)        // the free-text entry
    expect(answers[0].text).toBe('my own answer')  // the typed draft, not 'Search'
    expect(answers[1].label).toBe('Keep')
  })

  test('the pre-send summary shows exactly what each question delivers and jumps on tap', async ({ page }) => {
    const { traceId } = await seedMultiQuestion(page)
    await stubBridgeAnswer(page, traceId, { delivered: true, detail: 'submitted' })
    const sheet = await openMulti(page, traceId)

    const summary = sheet.locator('[data-testid="live-qa-summary"]')
    await expect(summary).toHaveCount(0)  // hidden until every question is answered

    await sheet.locator('[data-testid="live-qa-stage-free"]').click()
    await sheet.locator('[data-testid="live-qa-free-input"]').fill('a custom box')
    await sheet.locator('[data-testid="live-qa-next"]').click()
    await sheet.locator('[data-testid="live-qa-pick"]').nth(0).click()  // Q2 Bust
    await sheet.locator('[data-testid="live-qa-note-input"]').fill('gently')

    const rows = sheet.locator('[data-testid="live-qa-summary-row"]')
    await expect(summary).toBeVisible()
    await expect(rows.nth(0)).toContainText('✎')
    await expect(rows.nth(0)).toContainText('a custom box')
    // Option + note delivers as the free-text 'label — note' — the summary
    // mirrors the payload text exactly.
    await expect(rows.nth(1)).toContainText('Bust — gently')

    // Tapping a summary row jumps back to that question for revision.
    await rows.nth(0).click()
    await expect(sheet.locator('[data-testid="live-qa-step"]')).toContainText('Question 1 of 2')
  })

  test('changing the picked option drops a note written for the previous option', async ({ page }) => {
    const { traceId } = await seedMultiQuestion(page)
    const posts = await stubBridgeAnswer(page, traceId, { delivered: true, detail: 'submitted' })
    const sheet = await openMulti(page, traceId)

    await sheet.locator('[data-testid="live-qa-pick"]').nth(0).click()  // Q1 Composer
    await sheet.locator('[data-testid="live-qa-note-input"]').fill('only for Composer')
    await sheet.locator('[data-testid="live-qa-pick"]').nth(1).click()  // change mind → Search
    await expect(sheet.locator('[data-testid="live-qa-note-input"]')).toHaveValue('')

    await sheet.locator('[data-testid="live-qa-next"]').click()
    await sheet.locator('[data-testid="live-qa-pick"]').nth(0).click()  // Q2 Bust
    await sheet.locator('[data-testid="live-qa-send-all"]').click()

    await expect.poll(() => posts.length).toBe(1)
    const { answers } = posts[0]
    expect(answers[0].option_index).toBe(1)     // plain pick of Search…
    expect(answers[0].text).toBeUndefined()     // …not a noted answer carrying Composer's note
  })

  test('the typed-answer field auto-grows so long text stays visible', async ({ page }) => {
    const { traceId } = await seedMultiQuestion(page)
    await stubBridgeAnswer(page, traceId, { delivered: true, detail: 'submitted' })
    const sheet = await openMulti(page, traceId)

    await sheet.locator('[data-testid="live-qa-stage-free"]').click()
    const field = sheet.locator('[data-testid="live-qa-free-input"]')
    const empty = (await field.boundingBox()).height
    await field.fill('a long custom answer that definitely wraps across several lines '.repeat(2))
    const grown = (await field.boundingBox()).height
    expect(grown).toBeGreaterThan(empty * 1.8)  // multiple lines now visible
    // No internal scrolling below the CSS cap: the full text is on screen
    // (±4px for the border, which scrollHeight excludes under border-box).
    const clipped = await field.evaluate((el) => el.scrollHeight - el.clientHeight > 4)
    expect(clipped).toBe(false)
  })
})

// ---- review regressions -----------------------------------------------------
//
// Header/status lifecycle, outcome-row phrasing, filter-count, and
// connection-health invariants. The session-switch state-reset spec lives in
// live-picker.spec.js instead — it needs the picker's mocked-list fixture
// harness.

// Shifts a server-local naive timestamp (no Z/offset — see parseLocalIso) by
// deltaMs and re-renders it in the SAME naive shape, computed entirely via
// UTC getters on a Date built from Date.UTC of the parsed components. This
// keeps the result correct regardless of the TEST RUNNER's own timezone: the
// y/m/d/h/m/s digits are the only thing that matters, since parseLocalIso
// re-parses them as local-Date components on the browser side too.
function shiftServerTimestamp(iso, deltaMs) {
  const m = (iso || '').match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?/)
  if (!m) return iso
  const ms = m[7] ? parseInt(m[7].slice(0, 3).padEnd(3, '0'), 10) : 0
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6], ms) + deltaMs)
  const pad = (n, w = 2) => String(n).padStart(w, '0')
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T`
    + `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}.${pad(d.getUTCMilliseconds(), 3)}`
}

test.describe('Review regressions', () => {
  test('status flips to finished mid-view once a session.end span lands (applySummary refresh)', async ({ page }) => {
    const { traceId, sfx } = await postActiveSession(page)

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const header = page.locator('[data-testid="live-header"]')
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(rows(page).first()).toBeVisible({ timeout: 10_000 })
    await expect(header).toContainText('running', { timeout: 5_000 })
    await expect(nowZone).not.toHaveAttribute('data-state', 'finished')

    await post(page, [
      { trace_id: traceId, span_id: `end-${sfx}`, parent_id: null, name: 'session.end',
        start_time: new Date().toISOString(), attributes: { reason: 'clear', is_test: true } },
    ])

    // Two poll cycles at the 4s active cadence, plus fetch/render slack.
    await expect(header).toContainText('✓ finished', { timeout: 15_000 })
    await expect(nowZone).toHaveAttribute('data-state', 'finished', { timeout: 15_000 })
  })

  test('the NOW state follows the server phase and only changes on a poll (no client re-derivation)', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    // The server owns the phase verdict; the client renders it and never
    // re-derives idleness. While the server says working, the zone must never
    // flap to idle between polls; once the server flips to idle, it follows.
    let phase = 'working'
    await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
      const resp = await route.fetch()
      const json = await resp.json()
      await route.fulfill({
        response: resp,
        json: {
          ...json, bridge_reachable: true, bridge_pane: '%3',
          last_seen: quietLastSeen(), ...phaseFields(phase),
        },
      })
    })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toBeVisible({ timeout: 10_000 })

    // Server says working → zone never flaps to idle between polls.
    for (const wait of [3_000, 3_000, 3_000]) {
      await page.waitForTimeout(wait)
      await expect(nowZone).not.toHaveAttribute('data-state', 'idle')
    }

    // Server flips to idle → the next poll's applySummary follows it.
    phase = 'idle'
    await expect(nowZone).toHaveAttribute('data-state', 'idle', { timeout: 15_000 })
  })

  test('a permission.denied span renders a visible ✗ qa row with its reason (isQaSpan/SIGNAL_EXACT)', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'permission denied fixture', is_test: true } },
      { trace_id: traceId, span_id: `permdenied-${sfx}`, parent_id: null, name: 'permission.denied',
        start_time: now, status_code: 'ERROR',
        attributes: {
          tool_name: 'Bash', reason: 'user denied', command_preview: 'rm -rf /tmp/x', is_test: true,
        } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    // Default filters — no show-system toggle.
    const row = page.locator(`[data-testid="live-row"][data-span-id="permdenied-${sfx}"]`)
    await expect(row).toBeVisible({ timeout: 10_000 })
    await expect(row).toHaveAttribute('data-kind', 'qa')
    await expect(row).toContainText('Permission')
    await expect(row).toContainText('✗')
    await expect(row).toContainText('user denied')
  })

  test('rejected and failed tool rows say so instead of the success verb (toolOutcomeMain)', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'outcome-main fixture', is_test: true } },
      { trace_id: traceId, span_id: `rejected-${sfx}`, parent_id: null, name: 'tool.Write',
        start_time: now,
        attributes: {
          tool_name: 'Write', file_path: '/tmp/config.py', rejected: true,
          reject_reason: 'Read before Write', is_test: true,
        } },
      { trace_id: traceId, span_id: `failed-${sfx}`, parent_id: null, name: 'tool.failure',
        start_time: now, status_code: 'ERROR',
        attributes: { tool_name: 'Bash', command_preview: 'make test', is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)

    const rejectedRow = page.locator(`[data-testid="live-row"][data-span-id="rejected-${sfx}"]`)
    await expect(rejectedRow).toBeVisible({ timeout: 10_000 })
    await expect(rejectedRow).toContainText('blocked')
    await expect(rejectedRow).not.toContainText('Wrote')

    const failedRow = page.locator(`[data-testid="live-row"][data-span-id="failed-${sfx}"]`)
    await expect(failedRow).toBeVisible()
    await expect(failedRow).toContainText('Bash')
    await expect(failedRow).toContainText('failed')
    await expect(failedRow).not.toContainText('failure ·')
  })

  test('the category chip count respects the active search query, and selecting an emptied chip shows the filter-empty state', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    const MARKER = 'QUERYMARKER_UNIQUE'
    await post(page, [
      { trace_id: traceId, span_id: `prompt1-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: `${MARKER} first prompt`, is_test: true } },
      { trace_id: traceId, span_id: `prompt2-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: `${MARKER} second prompt`, is_test: true } },
      { trace_id: traceId, span_id: `bash1-${sfx}`, parent_id: null, name: 'tool.Bash',
        start_time: now, attributes: { command_preview: 'echo one', is_test: true } },
      { trace_id: traceId, span_id: `bash2-${sfx}`, parent_id: null, name: 'tool.Bash',
        start_time: now, attributes: { command_preview: 'echo two', is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(rows(page).first()).toBeVisible({ timeout: 10_000 })

    await page.locator('[data-testid="live-filter"]').click()
    const sheet = page.locator('[data-testid="live-sheet"]')
    await expect(sheet).toBeVisible({ timeout: 5_000 })

    // Baseline: the 'tool' chip counts the two tool.Bash spans before any
    // query narrows the set.
    const toolChip = sheet.locator('.live-chip', { hasText: 'tool' })
    await expect(toolChip.locator('.live-chip-n')).toHaveText('2')

    await page.getByPlaceholder(/search spans/i).fill(MARKER)
    // The query matches only the two prompts — the tool chip's count must
    // collapse to 0, not keep advertising the pre-query total.
    await expect(toolChip.locator('.live-chip-n')).toHaveText('0')

    await toolChip.click()
    await expect(page.getByText('no spans match the current filter')).toBeVisible({ timeout: 5_000 })
  })

  test('a connection-lost indicator appears after consecutive poll failures and clears on recovery (pollFailCount)', async ({ page }) => {
    const { traceId } = await postActiveSession(page)

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(rows(page).first()).toBeVisible({ timeout: 10_000 })

    const connLost = page.locator('[data-testid="live-conn-lost"]')
    await expect(connLost).toHaveCount(0)

    await page.route(`**/api/sessions/${traceId}/map*`, (route) => route.abort())
    // >=2 misses at the 4s active cadence (~8s) plus render slack.
    await expect(connLost).toBeVisible({ timeout: 15_000 })

    await page.unroute(`**/api/sessions/${traceId}/map*`)
    await expect(connLost).toBeHidden({ timeout: 15_000 })
  })

  test('a stale-but-"active" session reads "inactive", not "running" (stale computed)', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
      const resp = await route.fetch()
      const json = await resp.json()
      const staleLastSeen = json.server_now
        ? shiftServerTimestamp(json.server_now, -15 * 60 * 1000)
        : new Date(Date.now() - 15 * 60 * 1000).toISOString()
      // The header reads the SERVER phase now — a stale session's verdict is
      // inactive-stale; last_seen still feeds the client's poll cadence.
      await route.fulfill({
        response: resp,
        json: { ...json, last_seen: staleLastSeen, ...phaseFields('inactive-stale') },
      })
    })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const header = page.locator('[data-testid="live-header"]')
    await expect(header).toContainText('inactive', { timeout: 10_000 })

    const dot = page.locator('.live-status-dot')
    await expect(dot).toHaveClass(/live-status-stale/)
    await expect(dot).not.toHaveClass(/live-status-running/)
  })
})

// ---- tasks chip, agents, differentiated rows, ctx meter --------------------

test.describe('Header tasks chip + tasks sheet', () => {
  // task_list.final is computed server-side; posting the raw TaskCreate/
  // TaskUpdate spans exercises the real derivation, not a client re-tally.
  async function seedTasks(page) {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'tasks fixture', is_test: true } },
      { trace_id: traceId, span_id: `tc1-${sfx}`, parent_id: null, name: 'tool.TaskCreate',
        start_time: now, attributes: { task_id: '1', subject: 'Alpha task', status: 'pending',
          active_form: 'Doing alpha', is_test: true } },
      { trace_id: traceId, span_id: `tc2-${sfx}`, parent_id: null, name: 'tool.TaskCreate',
        start_time: now, attributes: { task_id: '2', subject: 'Beta task', status: 'pending',
          active_form: 'Doing beta now', is_test: true } },
      { trace_id: traceId, span_id: `tc3-${sfx}`, parent_id: null, name: 'tool.TaskCreate',
        start_time: now, attributes: { task_id: '3', subject: 'Gamma task', status: 'pending', is_test: true } },
      { trace_id: traceId, span_id: `tu1-${sfx}`, parent_id: null, name: 'tool.TaskUpdate',
        start_time: now, attributes: { task_id: '1', status: 'completed', is_test: true } },
      { trace_id: traceId, span_id: `tu2-${sfx}`, parent_id: null, name: 'tool.TaskUpdate',
        start_time: now, attributes: { task_id: '2', status: 'in_progress', is_test: true } },
    ])
    return { traceId, sfx }
  }

  test('chip shows done/total from the final snapshot and is accent-tinted while a task is in progress', async ({ page }) => {
    const { traceId } = await seedTasks(page)
    await page.goto(`/live/${traceId}`)
    await settle(page)
    const chip = page.locator('[data-testid="live-tasks-chip"]')
    await expect(chip).toBeVisible({ timeout: 10_000 })
    await expect(chip).toContainText('1/3')
    await expect(chip).toHaveClass(/live-hd-tasks-active/)
  })

  test('chip is hidden when the session used no tasks', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: new Date().toISOString(), attributes: { text: 'no tasks', is_test: true } },
    ])
    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[data-testid="live-tasks-chip"]')).toHaveCount(0)
  })

  test('tap → tasks sheet: counts strip; completed sorts last but stays visible and struck', async ({ page }) => {
    const { traceId } = await seedTasks(page)
    await page.goto(`/live/${traceId}`)
    await settle(page)
    const chip = page.locator('[data-testid="live-tasks-chip"]')
    await expect(chip).toBeVisible({ timeout: 10_000 })
    await chip.click()
    const sheet = page.locator('[data-testid="live-task-sheet"]')
    await expect(sheet).toBeVisible()
    await expect(page.locator('[data-testid="live-task-counts"]'))
      .toContainText('1 in progress · 1 open · 1 done')
    const items = page.locator('[data-testid="live-task-item"]')
    await expect(items).toHaveCount(3)
    // in_progress row carries its active_form line
    await expect(sheet).toContainText('Doing beta now')
    // completed 'Alpha task' still present, sorted LAST, and struck
    const last = items.last()
    await expect(last).toContainText('Alpha task')
    await expect(last).toHaveClass(/live-task-item-done/)
  })
})

test.describe('Header agents button + agents sheet', () => {
  async function seedAgents(page, { withStop } = { withStop: true }) {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    const spans = [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'agents fixture', is_test: true } },
      { trace_id: traceId, span_id: `agent-${sfx}`, parent_id: null, name: 'tool.Agent',
        start_time: now, attributes: { subagent_type: 'verifier',
          description: 'Verify acceptance items against the diff', tool_use_id: `tu-${sfx}`, is_test: true } },
      { trace_id: traceId, span_id: `substart-${sfx}`, parent_id: null, name: 'subagent.start',
        start_time: now, attributes: { agent_type: 'verifier', agent_id: `ag-${sfx}`, is_test: true } },
    ]
    if (withStop) {
      // Realistic: subagent.stop is an instantaneous point event — real
      // markers never carry duration_ms; the UI derives it from span times.
      spans.push({ trace_id: traceId, span_id: `substop-${sfx}`, parent_id: null,
        name: 'subagent.stop', start_time: now,
        attributes: { agent_type: 'verifier', agent_id: `ag-${sfx}`,
          result_preview: 'All checks passed', is_test: true } })
    }
    await post(page, spans)
    return { traceId, sfx }
  }

  test('a running agent → violet count badge; sheet lists it with type + description', async ({ page }) => {
    const { traceId } = await seedAgents(page, { withStop: false })
    await page.goto(`/live/${traceId}`)
    await settle(page)
    const badge = page.locator('[data-testid="live-agents-badge"]')
    await expect(badge).toBeVisible({ timeout: 10_000 })
    await expect(badge).toHaveText('1')
    await page.locator('[data-testid="live-agents-btn"]').click()
    const sheet = page.locator('[data-testid="live-agent-sheet"]')
    await expect(sheet).toBeVisible()
    const card = page.locator('[data-testid="live-agent-card"]').first()
    await expect(card).toContainText('verifier')
    await expect(card).toContainText('Verify acceptance items')
  })

  test('a finished agent → no badge; Finished group is collapsed by default and expands to the result', async ({ page }) => {
    const { traceId } = await seedAgents(page, { withStop: true })
    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[data-testid="live-agents-badge"]')).toHaveCount(0)
    await page.locator('[data-testid="live-agents-btn"]').click()
    await expect(page.locator('[data-testid="live-agent-sheet"]')).toBeVisible()
    // collapsed by default
    await expect(page.locator('[data-testid="live-agent-finished"]')).toHaveCount(0)
    const toggle = page.locator('[data-testid="live-agent-finished-toggle"]')
    await expect(toggle).toContainText('Finished (1)')
    await toggle.click()
    await expect(page.locator('[data-testid="live-agent-finished"]')).toBeVisible()
    await expect(page.locator('[data-testid="live-agent-sheet"]')).toContainText('All checks passed')
  })

  test('empty state when no agents were launched', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: new Date().toISOString(), attributes: { text: 'no agents', is_test: true } },
    ])
    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[data-testid="live-agents-badge"]')).toHaveCount(0)
    await page.locator('[data-testid="live-agents-btn"]').click()
    await expect(page.locator('[data-testid="live-agent-empty"]'))
      .toContainText('no agents launched this session')
  })
})

test.describe('Differentiated tail rows', () => {
  test('agent launch / subagent stop / workflow phase / task events get human phrasing — no raw span-type names', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'diff-rows fixture', is_test: true } },
      { trace_id: traceId, span_id: `phase-${sfx}`, parent_id: null, name: 'workflow.phase',
        start_time: now, attributes: { title: 'verify', index: 2, is_test: true } },
      { trace_id: traceId, span_id: `agent-${sfx}`, parent_id: null, name: 'tool.Agent',
        start_time: now, attributes: { subagent_type: 'builder', description: 'Port the design',
          tool_use_id: `tu-${sfx}`, is_test: true } },
      { trace_id: traceId, span_id: `substop-${sfx}`, parent_id: null, name: 'subagent.stop',
        start_time: now,
        attributes: { agent_type: 'builder', agent_id: `ag-${sfx}`,
          result_preview: 'done building', is_test: true } },
      { trace_id: traceId, span_id: `tc-${sfx}`, parent_id: null, name: 'tool.TaskCreate',
        start_time: now, attributes: { task_id: '1', subject: 'first task', status: 'completed', is_test: true } },
    ])
    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })

    const phase = page.locator(`[data-testid="live-row"][data-span-id="phase-${sfx}"]`)
    await expect(phase).toHaveAttribute('data-kind', 'phase')
    await expect(phase).toContainText('Phase 2 · verify')

    const agent = page.locator(`[data-testid="live-row"][data-span-id="agent-${sfx}"]`)
    await expect(agent).toContainText('Agent · builder')
    await expect(agent).toContainText('Port the design')

    await expect(page.locator(`[data-testid="live-row"][data-span-id="substop-${sfx}"]`))
      .toContainText('builder finished')
    await expect(page.locator(`[data-testid="live-row"][data-span-id="tc-${sfx}"]`))
      .toContainText('first task')

    // No raw span-type name may reach the default view.
    const tail = page.locator('[data-testid="live-tail"]')
    await expect(tail).not.toContainText('tool.Agent')
    await expect(tail).not.toContainText('workflow.phase')
    await expect(tail).not.toContainText('tool.TaskCreate')
    await expect(tail).not.toContainText('subagent.stop')
  })
})

test.describe('Header meta line + ctx meter', () => {
  async function withCtx(page, traceId, patch) {
    await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
      const resp = await route.fetch()
      const json = await resp.json()
      await route.fulfill({ response: resp, json: { ...json, ...patch } })
    })
  }

  test('meta line shows repo · model and the ctx meter goes amber past 80%', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    await withCtx(page, traceId, { repo: 'regin', model: 'claude-opus-4-8', context_pct: 87 })
    await page.goto(`/live/${traceId}`)
    await settle(page)
    const meta = page.locator('[data-testid="live-hd-meta"]')
    await expect(meta).toBeVisible({ timeout: 10_000 })
    await expect(meta).toContainText('regin')
    await expect(meta).toContainText('opus 4.8')
    const ctx = meta.locator('[data-testid="live-ctx-meter"]')
    await expect(ctx).toContainText('ctx 87%')
    await expect(ctx).toHaveClass(/live-ctx-warn/)
  })

  test('ctx meter stays neutral (no amber) at/under 80%', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    await withCtx(page, traceId, { repo: 'regin', model: 'claude-opus-4-8', context_pct: 62 })
    await page.goto(`/live/${traceId}`)
    await settle(page)
    const ctx = page.locator('[data-testid="live-hd-meta"] [data-testid="live-ctx-meter"]')
    await expect(ctx).toContainText('ctx 62%', { timeout: 10_000 })
    await expect(ctx).not.toHaveClass(/live-ctx-warn/)
  })
})

// ---- per-agent span scoping -------------------------------------------------
//
// The partition rule under test is purely attribute-based: a span is
// agent-internal iff attributes.agent_id is set (liveRows.inScope). These
// fixtures post the internal spans as roots so they load deterministically
// into the tail; the real delivery path (deep-children of the subagent.start
// root returning agent_id-tagged spans) was verified against db/regin.db
// out-of-band and needs no live subagent to exercise the client partition.

test.describe('Per-agent span scoping', () => {
  const MAIN_CMD = 'echo MAIN_ONLY_CMD_MARKER'
  const INT_FILE = 'src/AGENT_INTERNAL_MARKER.js'

  // A main-agent turn + one running subagent whose internal spans (same
  // agent_id) sit in the tail. Optionally a finished subagent with NO
  // internal spans, to exercise the empty-scope terminal state.
  async function seedScoped(page, { emptyAgent = false } = {}) {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    const later = new Date(Date.now() + 2000).toISOString()
    const agId = `ag-run-${sfx}`
    const spans = [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'scoping fixture', is_test: true } },
      // Main-agent activity (no agent_id) — main scope only.
      { trace_id: traceId, span_id: `mainbash-${sfx}`, parent_id: null, name: 'tool.Bash',
        start_time: now, attributes: { command_preview: MAIN_CMD, is_test: true } },
      // Launch marker (carries description for the roster) + start marker.
      { trace_id: traceId, span_id: `agent-${sfx}`, parent_id: null, name: 'tool.Agent',
        start_time: now, attributes: { subagent_type: 'explorer',
          description: 'Map the breakpoints', tool_use_id: `tu-${sfx}`, agent_id: agId, is_test: true } },
      { trace_id: traceId, span_id: `substart-${sfx}`, parent_id: null, name: 'subagent.start',
        start_time: now, attributes: { agent_type: 'explorer', agent_id: agId, is_test: true } },
      // Subagent-internal spans (agent_id === agId) — agent scope only.
      { trace_id: traceId, span_id: `int-read-${sfx}`, parent_id: null, name: 'tool.Read',
        start_time: later, attributes: { file_path: INT_FILE, agent_id: agId, is_test: true } },
      // Main-agent's own latest response (newest) so the main NOW zone is stable.
      { trace_id: traceId, span_id: `mainresp-${sfx}`, parent_id: null, name: 'assistant_response',
        start_time: later, attributes: { text: 'MAIN_RESP_MARKER', is_test: true } },
    ]
    if (emptyAgent) {
      // A finished subagent whose internal spans were never captured.
      // Start before stop — pairing is by ingest order, as in real ingest.
      spans.push({ trace_id: traceId, span_id: `substart2-${sfx}`, parent_id: null,
        name: 'subagent.start', start_time: now,
        attributes: { agent_type: 'ghost', agent_id: `ag-empty-${sfx}`, is_test: true } })
      spans.push({ trace_id: traceId, span_id: `substop2-${sfx}`, parent_id: null,
        name: 'subagent.stop', start_time: now,
        attributes: { agent_type: 'ghost', agent_id: `ag-empty-${sfx}`,
          result_preview: 'nothing captured', is_test: true } })
    }
    await post(page, spans)
    return { traceId, sfx, agId }
  }

  const tail = (page) => page.locator('[data-testid="live-tail"]')
  const scopeBar = (page) => page.locator('[data-testid="live-scope-bar"]')

  async function openAgents(page) {
    await page.locator('[data-testid="live-agents-btn"]').click()
    await expect(page.locator('[data-testid="live-agent-sheet"]')).toBeVisible({ timeout: 5_000 })
  }

  test('scope entry from the sheet: tapping an agent scopes the tail and closes the sheet', async ({ page }) => {
    const { traceId } = await seedScoped(page)
    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })

    await openAgents(page)
    // Primary card tap = scope (NOT the chevron, which opens span detail).
    await page.locator('[data-testid="live-agent-card"]').first().click()

    await expect(scopeBar(page)).toBeVisible({ timeout: 5_000 })
    await expect(page.locator('[data-testid="live-agent-sheet"]')).toBeHidden()
  })

  test('the chevron opens the agent detail sheet, not the raw span detail, and does not scope', async ({ page }) => {
    const { traceId } = await seedScoped(page)
    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })

    await openAgents(page)
    await page.locator('[data-testid="live-agent-info"]').first().click()
    // The AGENT detail sheet (roster-sourced) opens — distinct from tapping
    // the card body (which scopes) and from a raw span-detail sheet.
    const detail = page.locator('[data-testid="live-agent-detail"]')
    await expect(detail).toBeVisible()
    await expect(detail).toContainText('explorer')
    await expect(page.locator('[data-testid="live-agent-sheet"]')).toBeHidden()
    await expect(scopeBar(page)).toHaveCount(0)
  })

  test('partition: agent-internal rows are absent in main and present in scope; main-only rows invert', async ({ page }) => {
    const { traceId } = await seedScoped(page)
    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(tail(page)).toBeVisible({ timeout: 10_000 })

    // MAIN scope: the main command shows; the agent-internal file does NOT.
    await expect(tail(page)).toContainText('MAIN_ONLY_CMD_MARKER')
    await expect(tail(page)).not.toContainText('AGENT_INTERNAL_MARKER')
    // The subagent launch/start marker DOES stand in for the agent in main.
    await expect(tail(page)).toContainText('explorer')

    await openAgents(page)
    await page.locator('[data-testid="live-agent-card"]').first().click()
    await expect(scopeBar(page)).toBeVisible({ timeout: 5_000 })

    // AGENT scope: the internal file shows; the main-only command is gone.
    await expect(tail(page)).toContainText('AGENT_INTERNAL_MARKER')
    await expect(tail(page)).not.toContainText('MAIN_ONLY_CMD_MARKER')
  })

  test('scope bar content + ✕ restores the main tail and its exact scroll position', async ({ page }) => {
    const { traceId } = await seedScoped(page)
    // Extra main rows so the tail is scrollable.
    const sfx2 = traceId.slice(0, 8)
    const filler = []
    for (let i = 0; i < 30; i++) {
      filler.push({ trace_id: traceId, span_id: `fill-${sfx2}-${i}`, parent_id: null,
        name: 'tool.Read', start_time: new Date().toISOString(),
        attributes: { file_path: `src/fill${i}.js`, is_test: true } })
    }
    await post(page, filler)

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(tail(page)).toBeVisible({ timeout: 10_000 })

    // Land the main tail at a non-zero, non-bottom scroll position.
    await tail(page).evaluate((el) => { el.scrollTop = Math.floor(el.scrollHeight / 3) })
    await page.waitForTimeout(150)
    const before = await tail(page).evaluate((el) => el.scrollTop)
    expect(before).toBeGreaterThan(0)

    await openAgents(page)
    await page.locator('[data-testid="live-agent-card"]').first().click()
    const bar = scopeBar(page)
    await expect(bar).toBeVisible({ timeout: 5_000 })
    await expect(bar).toContainText('explorer')
    await expect(bar).toContainText('running')

    // ✕ returns to main and restores the exact pre-scope scroll.
    await page.locator('[data-testid="live-scope-exit"]').click()
    await expect(bar).toHaveCount(0)
    await expect(tail(page)).toContainText('MAIN_ONLY_CMD_MARKER')
    await page.waitForTimeout(150)
    const after = await tail(page).evaluate((el) => el.scrollTop)
    expect(after, 'exiting scope must restore the main tail scroll position').toBe(before)
  })

  test('NOW zone while scoped: agent status + back-to-main, no composer', async ({ page }) => {
    const { traceId } = await seedScoped(page)
    // Make the session bridge-reachable + idle so a composer WOULD show in main
    // scope — proving the scoped zone suppresses it, not just the bridge gate.
    await bridgeReachableMap(page, traceId)
    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })

    await openAgents(page)
    await page.locator('[data-testid="live-agent-card"]').first().click()
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'scoped', { timeout: 5_000 })
    await expect(nowZone).toContainText('agent running')
    await expect(page.locator('[data-testid="live-now-back"]')).toBeVisible()
    // The bridge composer must be absent while scoped.
    await expect(page.locator('[data-testid="live-composer"]')).toHaveCount(0)

    // back-to-main also exits the scope.
    await page.locator('[data-testid="live-now-back"]').click()
    await expect(scopeBar(page)).toHaveCount(0)
  })

  test('empty-scope terminal state: an agent with no captured spans shows "no spans captured"', async ({ page }) => {
    const { traceId } = await seedScoped(page, { emptyAgent: true })
    // Pageable history existing says nothing about THIS agent's spans — the
    // hint must read the roster's span_count, not hasMoreOlder.
    await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
      const resp = await route.fetch()
      const json = await resp.json()
      await route.fulfill({ response: resp, json: { ...json, has_more_older: true } })
    })
    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })

    await openAgents(page)
    // The empty (ghost) agent is finished → expand the Finished group first.
    await page.locator('[data-testid="live-agent-finished-toggle"]').click()
    await page.locator('[data-testid="live-agent-finished"] [data-testid="live-agent-card"]')
      .first().click()

    await expect(scopeBar(page)).toBeVisible({ timeout: 5_000 })
    const empty = page.locator('[data-testid="live-scope-empty"]')
    await expect(empty).toBeVisible()
    await expect(empty).toContainText('no spans captured for this agent')
    await expect(empty).not.toContainText('spans not loaded')
    await expect(page.locator('[data-testid="live-scope-load"]')).toHaveCount(0)
    // Terminal, not a spinner.
    await expect(page.locator('[data-testid="live-now"] .live-spinner')).toHaveCount(0)
  })
})

// ---- agent lifecycle — interrupted / stale / resumed ------------------------
//
// A denied/interrupted Task launch never emits subagent.stop: the launch
// stays a PENDING placeholder and a tooldeny-* marker (name tool.Agent,
// status ERROR, same tool_use_id) is the deterministic interruption signal.
// A resumed agent (SendMessage) emits a NEW subagent.start under the SAME
// agent_id — pairing must be chronological segments, or the old stop
// swallows the resumed start. Neither may ever show "running forever" /
// "finished" respectively.

test.describe('Agent lifecycle — interrupted / stale / resumed', () => {
  async function openAgents(page) {
    await page.locator('[data-testid="live-agents-btn"]').click()
    await expect(page.locator('[data-testid="live-agent-sheet"]')).toBeVisible({ timeout: 5_000 })
  }
  const badge = (page) => page.locator('[data-testid="live-agents-badge"]')
  const finishedToggle = (page) => page.locator('[data-testid="live-agent-finished-toggle"]')
  const agentStatus = (page) => page.locator('[data-testid="live-agent-status"]')

  // The exact dogfooded failure shape (trace 04f5c665, agent adf9bdfa…):
  // PENDING tool.Agent launch + tooldeny-* ERROR marker sharing its
  // tool_use_id + a subagent.start that never gets a stop.
  async function seedInterrupted(page) {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'interrupted-agent fixture', is_test: true } },
      { trace_id: traceId, span_id: `pending-tu-${sfx}`, parent_id: null, name: 'tool.Agent',
        start_time: now, status_code: 'PENDING',
        attributes: { tool_name: 'Agent', tool_use_id: `tu-${sfx}`, subagent_type: 'builder',
          description: 'Build the thing', is_test: true } },
      { trace_id: traceId, span_id: `tooldeny-tu-${sfx}`, parent_id: null, name: 'tool.Agent',
        start_time: now, status_code: 'ERROR',
        attributes: { tool_name: 'Agent', tool_use_id: `tu-${sfx}`, denied: true,
          deny_kind: 'deny', agent_type: 'claude',
          tool_input: { subagent_type: 'builder', description: 'Build the thing' }, is_test: true } },
      { trace_id: traceId, span_id: `substart-${sfx}`, parent_id: null, name: 'subagent.start',
        start_time: now, attributes: { agent_type: 'builder', agent_id: `ag-int-${sfx}`, is_test: true } },
      { trace_id: traceId, span_id: `resp-${sfx}`, parent_id: null, name: 'assistant_response',
        start_time: now, attributes: { text: 'main moved on', is_test: true } },
    ])
    return { traceId, sfx }
  }

  test('deny marker → interrupted: excluded from the badge, shown in the disclosure group', async ({ page }) => {
    const { traceId } = await seedInterrupted(page)
    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })

    // Not running: no violet badge at all.
    await expect(badge(page)).toHaveCount(0)

    await openAgents(page)
    await expect(page.locator('[data-testid="live-agent-sheet"]')).toContainText('no agents running')
    await expect(finishedToggle(page)).toContainText('Finished (1)')
    await finishedToggle(page).click()
    await expect(agentStatus(page)).toContainText('interrupted')
    await expect(agentStatus(page)).toHaveClass(/live-agent-time-warn/)
  })

  test('staleness fallback: a silent unstopped agent goes stale while a recent one stays running', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const t20 = new Date(Date.now() - 20 * 60_000).toISOString()
    const t15 = new Date(Date.now() - 15 * 60_000).toISOString()
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: t20, attributes: { text: 'stale-agent fixture', is_test: true } },
      // Old unstopped agent: last own span 15 min ago, then silence.
      { trace_id: traceId, span_id: `substart-old-${sfx}`, parent_id: null, name: 'subagent.start',
        start_time: t20, attributes: { agent_type: 'explorer', agent_id: `ag-old-${sfx}`, is_test: true } },
      { trace_id: traceId, span_id: `int-old-${sfx}`, parent_id: null, name: 'tool.Read',
        start_time: t15, attributes: { file_path: 'src/old.js', agent_id: `ag-old-${sfx}`, is_test: true } },
      // Recent unstopped agent: must NEVER be flagged.
      { trace_id: traceId, span_id: `substart-new-${sfx}`, parent_id: null, name: 'subagent.start',
        start_time: now, attributes: { agent_type: 'checker', agent_id: `ag-new-${sfx}`, is_test: true } },
      // Main agent kept going past the stale agent's last sign of life.
      { trace_id: traceId, span_id: `resp-${sfx}`, parent_id: null, name: 'assistant_response',
        start_time: now, attributes: { text: 'main still working', is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(badge(page)).toBeVisible({ timeout: 10_000 })
    await expect(badge(page)).toHaveText('1')

    await openAgents(page)
    // The recent agent rides the running group…
    const running = page.locator('[data-testid="live-agent-card"]').first()
    await expect(running).toContainText('checker')
    // …the silent one sits in the disclosure group as stale, with its last
    // sign of life (compact "stale · HH:MM" — the sheet's time column is
    // narrow at 375px), not a ticking elapsed.
    await finishedToggle(page).click()
    await expect(agentStatus(page)).toContainText(/stale · \d{2}:\d{2}/)
  })

  test('scoped to an interrupted agent: NOW zone says "agent interrupted", scope bar agrees', async ({ page }) => {
    const { traceId } = await seedInterrupted(page)
    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })

    await openAgents(page)
    await finishedToggle(page).click()
    await page.locator('[data-testid="live-agent-finished"] [data-testid="live-agent-card"]')
      .first().click()

    const bar = page.locator('[data-testid="live-scope-bar"]')
    await expect(bar).toBeVisible({ timeout: 5_000 })
    await expect(bar).toContainText('interrupted')
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'scoped')
    await expect(nowZone).toContainText('agent interrupted')
    await expect(nowZone).not.toContainText('agent running')
  })

  test('main NOW zone ignores an agent-internal assistant_response (partitioned projection)', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    const later = new Date(Date.now() + 2000).toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'main-now partition fixture', is_test: true } },
      { trace_id: traceId, span_id: `mainresp-${sfx}`, parent_id: null, name: 'assistant_response',
        start_time: now, attributes: { text: 'MAIN_RESP_MARKER', is_test: true } },
      { trace_id: traceId, span_id: `substart-${sfx}`, parent_id: null, name: 'subagent.start',
        start_time: now, attributes: { agent_type: 'builder', agent_id: `ag-${sfx}`, is_test: true } },
      // NEWER than the main response, but agent-internal — must not surface.
      { trace_id: traceId, span_id: `intresp-${sfx}`, parent_id: null, name: 'assistant_response',
        start_time: later,
        attributes: { text: 'AGENT_INTERNAL_RESP_MARKER', agent_id: `ag-${sfx}`, is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toBeVisible({ timeout: 10_000 })
    await expect(nowZone).toContainText('MAIN_RESP_MARKER')
    await expect(nowZone).not.toContainText('AGENT_INTERNAL_RESP_MARKER')
  })

  // Resumed agent: same agent_id, [start, stop, start] in ingest order with
  // fresh own spans → the roster's ONE row is RUNNING off the second start.
  test('resumed agent [start,stop,start] + fresh spans → running, elapsed from the second start', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const agId = `ag-res-${sfx}`
    const t30 = new Date(Date.now() - 30 * 60_000).toISOString()
    const t25 = new Date(Date.now() - 25 * 60_000).toISOString()
    const t2 = new Date(Date.now() - 2 * 60_000).toISOString()
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: t30, attributes: { text: 'resumed-agent fixture', is_test: true } },
      { trace_id: traceId, span_id: `substart1-${sfx}`, parent_id: null, name: 'subagent.start',
        start_time: t30, attributes: { agent_type: 'builder', agent_id: agId, is_test: true } },
      { trace_id: traceId, span_id: `substop1-${sfx}`, parent_id: null, name: 'subagent.stop',
        start_time: t25,
        attributes: { agent_type: 'builder', agent_id: agId, result_preview: 'segment one done', is_test: true } },
      { trace_id: traceId, span_id: `substart2-${sfx}`, parent_id: null, name: 'subagent.start',
        start_time: t2, attributes: { agent_type: 'builder', agent_id: agId, is_test: true } },
      { trace_id: traceId, span_id: `int-${sfx}`, parent_id: null, name: 'tool.Read',
        start_time: now, attributes: { file_path: 'src/fresh.js', agent_id: agId, is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    // ONE running agent — the old stop must not swallow the resumed start.
    await expect(badge(page)).toBeVisible({ timeout: 10_000 })
    await expect(badge(page)).toHaveText('1')

    await openAgents(page)
    const card = page.locator('[data-testid="live-agent-card"]').first()
    await expect(card).toContainText('builder')
    // Elapsed anchors to the SECOND start (~2 min), not the first (~30 min).
    await expect(card).toContainText(/[12]m\d{2}s/)
    await expect(card).not.toContainText(/(2[89]|3[01])m\d{2}s/)
    // One row per agent_id: no duplicate segment rows.
    await expect(page.locator('[data-testid="live-agent-card"]')).toHaveCount(1)
  })

  test('resumed agent [start,stop,start,stop] → finished with the SECOND segment\'s duration', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const agId = `ag-res2-${sfx}`
    const t30 = new Date(Date.now() - 30 * 60_000).toISOString()
    const t25 = new Date(Date.now() - 25 * 60_000).toISOString()
    const t20 = new Date(Date.now() - 20 * 60_000).toISOString()
    const t18 = new Date(Date.now() - 18 * 60_000).toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: t30, attributes: { text: 'resumed-finished fixture', is_test: true } },
      { trace_id: traceId, span_id: `substart1-${sfx}`, parent_id: null, name: 'subagent.start',
        start_time: t30, attributes: { agent_type: 'builder', agent_id: agId, is_test: true } },
      { trace_id: traceId, span_id: `substop1-${sfx}`, parent_id: null, name: 'subagent.stop',
        start_time: t25,
        attributes: { agent_type: 'builder', agent_id: agId, result_preview: 'segment one done', is_test: true } },
      { trace_id: traceId, span_id: `substart2-${sfx}`, parent_id: null, name: 'subagent.start',
        start_time: t20, attributes: { agent_type: 'builder', agent_id: agId, is_test: true } },
      { trace_id: traceId, span_id: `substop2-${sfx}`, parent_id: null, name: 'subagent.stop',
        start_time: t18,
        attributes: { agent_type: 'builder', agent_id: agId, result_preview: 'segment two done', is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })
    await expect(badge(page)).toHaveCount(0)

    await openAgents(page)
    await expect(finishedToggle(page)).toContainText('Finished (1)')
    await finishedToggle(page).click()
    const card = page.locator('[data-testid="live-agent-finished"] [data-testid="live-agent-card"]').first()
    // Latest segment's outcome: result + 2m duration, never segment one's 5m.
    await expect(card).toContainText('segment two done')
    await expect(card).toContainText('2m')
    await expect(card).not.toContainText('5m')
  })

  test('scoped to a roster agent whose spans are not in the window → "spans not loaded" hint', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'unloaded-scope fixture', is_test: true } },
    ])
    // The roster is server truth over the WHOLE session; simulate an agent
    // whose 42 internal spans all sit outside the loaded window.
    await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
      const resp = await route.fetch()
      const json = await resp.json()
      await route.fulfill({ response: resp, json: { ...json, agent_roster: [
        { agent_id: `ag-far-${sfx}`, agent_type: 'digger',
          description: 'Dig through old history', status: 'stale',
          started_at: now, last_seen: now, duration_ms: null,
          result_preview: '', start_span_id: null, span_count: 42 },
      ] } })
    })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })

    await openAgents(page)
    await finishedToggle(page).click()
    await page.locator('[data-testid="live-agent-finished"] [data-testid="live-agent-card"]')
      .first().click()

    const empty = page.locator('[data-testid="live-scope-empty"]')
    await expect(empty).toBeVisible({ timeout: 5_000 })
    // Distinguished from "no spans captured": the roster KNOWS spans exist.
    await expect(empty).toContainText('spans not loaded — load earlier history to view')
    // The fold row stays hidden in scope — the not-loaded state carries its
    // own load action instead.
    await expect(page.locator('[data-testid="live-scope-load"]')).toBeVisible()
  })

  test('an agent whose only fresh span is a PENDING placeholder stays running; span_count still excludes it', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const agId = `ag-pend-${sfx}`
    const t20 = new Date(Date.now() - 20 * 60_000).toISOString()
    const t15 = new Date(Date.now() - 15 * 60_000).toISOString()
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: t20, attributes: { text: 'pending-roster fixture', is_test: true } },
      { trace_id: traceId, span_id: `substart-${sfx}`, parent_id: null, name: 'subagent.start',
        start_time: t20, attributes: { agent_type: 'builder', agent_id: agId, is_test: true } },
      // Last RESOLVED span is 15 min old — without PENDING liveness this
      // agent would misread stale mid-long-tool.
      { trace_id: traceId, span_id: `int-ok-${sfx}`, parent_id: null, name: 'tool.Read',
        start_time: t15, attributes: { file_path: 'src/done.js', agent_id: agId, is_test: true } },
      { trace_id: traceId, span_id: `pending-tu-${sfx}`, parent_id: null, name: 'tool.Bash',
        start_time: now, status_code: 'PENDING',
        attributes: { command_preview: 'sleep 999', tool_use_id: `tu-${sfx}`,
          agent_id: agId, is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })
    await expect(badge(page)).toHaveText('1')

    const headers = await authHeaders(page)
    const resp = await page.request.get(
      `/api/sessions/${traceId}/map?shallow=1&limit=5`, { headers })
    expect(resp.ok()).toBeTruthy()
    const roster = (await resp.json()).agent_roster || []
    expect(roster).toHaveLength(1)
    expect(roster[0].status, 'the PENDING placeholder is live activity').toBe('running')
    expect(roster[0].span_count, 'PENDING placeholder must not count').toBe(1)
    expect(roster[0].last_seen, 'liveness reads the PENDING row').toBe(now)
  })

  test('age alone stales a lost-stop agent on a LIVE session, even as the session\'s newest span', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const t20 = new Date(Date.now() - 20 * 60_000).toISOString()
    const t15 = new Date(Date.now() - 15 * 60_000).toISOString()
    await post(page, [
      // The MAIN agent's last span is OLDER than the agent's — the old
      // `seen < latest_main` guard read this as running forever.
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: t20, attributes: { text: 'lost-stop fixture', is_test: true } },
      { trace_id: traceId, span_id: `substart-${sfx}`, parent_id: null, name: 'subagent.start',
        start_time: t20, attributes: { agent_type: 'digger', agent_id: `ag-${sfx}`, is_test: true } },
      { trace_id: traceId, span_id: `int-${sfx}`, parent_id: null, name: 'tool.Read',
        start_time: t15, attributes: { file_path: 'src/last.js', agent_id: `ag-${sfx}`, is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })
    await expect(badge(page)).toHaveCount(0)

    await openAgents(page)
    await expect(page.locator('[data-testid="live-agent-sheet"]')).toContainText('no agents running')
    await finishedToggle(page).click()
    await expect(agentStatus(page)).toContainText('stale')
  })

  test('an unanchored deny never claims a same-type running agent', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'unanchored-deny fixture', is_test: true } },
      // A deny with NO matching launch placeholder (its agent never
      // started) — it must claim nothing.
      { trace_id: traceId, span_id: `tooldeny-tux-${sfx}`, parent_id: null, name: 'tool.Agent',
        start_time: now, status_code: 'ERROR',
        attributes: { tool_name: 'Agent', tool_use_id: `tux-${sfx}`, denied: true,
          deny_kind: 'deny', agent_type: 'claude',
          tool_input: { subagent_type: 'builder', description: 'Never started' }, is_test: true } },
      // An innocent same-type running agent.
      { trace_id: traceId, span_id: `substart-${sfx}`, parent_id: null, name: 'subagent.start',
        start_time: now, attributes: { agent_type: 'builder', agent_id: `ag-run-${sfx}`, is_test: true } },
      { trace_id: traceId, span_id: `int-${sfx}`, parent_id: null, name: 'tool.Read',
        start_time: now, attributes: { file_path: 'src/busy.js', agent_id: `ag-run-${sfx}`, is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(badge(page)).toBeVisible({ timeout: 10_000 })
    await expect(badge(page)).toHaveText('1')

    const headers = await authHeaders(page)
    const resp = await page.request.get(
      `/api/sessions/${traceId}/map?shallow=1&limit=5`, { headers })
    const roster = (await resp.json()).agent_roster || []
    expect(roster).toHaveLength(1)
    expect(roster[0].status, 'the deny has no anchor chain to this agent').toBe('running')
  })

  test('a subagent blocked on AskUserQuestion reads waiting; the idle NOW zone says so', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'waiting-agent fixture', is_test: true } },
      { trace_id: traceId, span_id: `resp-${sfx}`, parent_id: null, name: 'assistant_response',
        start_time: now, attributes: { text: 'main is done for now', is_test: true } },
      { trace_id: traceId, span_id: `substart-${sfx}`, parent_id: null, name: 'subagent.start',
        start_time: now, attributes: { agent_type: 'builder', agent_id: `ag-${sfx}`, is_test: true } },
      // The agent's question is agent-internal — INVISIBLE to the main
      // NOW-zone projection; only the roster can surface it.
      { trace_id: traceId, span_id: `pending-ask-${sfx}`, parent_id: null,
        name: 'tool.AskUserQuestion', start_time: now, status_code: 'PENDING',
        attributes: { tool_name: 'AskUserQuestion', tool_use_id: `tu-${sfx}`,
          agent_id: `ag-${sfx}`, is_test: true,
          questions: [{ question: 'Which file?', options: [{ label: 'A' }, { label: 'B' }] }] } },
    ])
    // Main idle, the subagent blocked on input — the server verdict the main
    // NOW zone reads (agent_phase.main). The roster (real backend) still
    // surfaces the waiting subagent for the badge + note.
    await bridgeReachableMap(page, traceId, {
      agentPhase: { main: 'idle', [`ag-${sfx}`]: 'waiting-input' },
    })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    // Waiting counts as running for the badge.
    await expect(badge(page)).toHaveText('1', { timeout: 10_000 })

    // The MAIN zone goes idle (the subagent's ask is not its concern)…
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'idle', { timeout: 20_000 })
    // …but the sub-note surfaces the blocked agent, amber.
    const note = page.locator('[data-testid="live-now-agents-note"]')
    await expect(note).toBeVisible()
    await expect(note).toContainText('1 agent running · 1 waiting')
    await expect(note).toHaveClass(/live-now-agents-warn/)

    // Tapping the note opens the agents sheet; the row reads waiting, amber.
    await note.click()
    await expect(page.locator('[data-testid="live-agent-sheet"]')).toBeVisible()
    await expect(agentStatus(page)).toContainText('waiting')
    await expect(agentStatus(page)).toHaveClass(/live-agent-time-warn/)
  })

  test('an ended session cannot have a running agent: unstopped agent reads stale, no badge', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'ended-roster fixture', is_test: true } },
      // Unstopped agent whose last span POSTDATES the last main span — the
      // age-based stale check alone would read "running" forever.
      { trace_id: traceId, span_id: `substart-${sfx}`, parent_id: null, name: 'subagent.start',
        start_time: now, attributes: { agent_type: 'builder', agent_id: `ag-${sfx}`, is_test: true } },
      { trace_id: traceId, span_id: `int-${sfx}`, parent_id: null, name: 'tool.Read',
        start_time: new Date(Date.now() + 1000).toISOString(),
        attributes: { file_path: 'src/late.js', agent_id: `ag-${sfx}`, is_test: true } },
      { trace_id: traceId, span_id: `end-${sfx}`, parent_id: null, name: 'session.end',
        start_time: now, attributes: { reason: 'clear', is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })
    await expect(badge(page)).toHaveCount(0)

    await openAgents(page)
    await expect(page.locator('[data-testid="live-agent-sheet"]')).toContainText('no agents running')
    await expect(finishedToggle(page)).toContainText('Finished (1)')
    await finishedToggle(page).click()
    await expect(agentStatus(page)).toContainText('stale')
  })
})

// ---- Slice C: server-phase rendering, interrupts, queued/steer, scoped prompt
//
// Phase is the single state truth (agent_phase.main → header + main NOW zone).
// These pin the render contract the phase model enables: a stale payload can
// never show a ticking pending, an interrupted tool row says so, queued/steer
// prompts are visible, and a scoped tail opens with the agent's task prompt.

test.describe('Phase-driven state (Slice C)', () => {
  test('inactive-stale + a leftover PENDING tool → header "inactive", NEVER a ticking tool state', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'inactive-with-pending fixture', is_test: true } },
      // A stuck PENDING tool the server would demote — but even un-demoted,
      // the phase guard must stop the NOW zone ticking it.
      { trace_id: traceId, span_id: `pending-tu-${sfx}`, parent_id: null, name: 'tool.Bash',
        start_time: now, status_code: 'PENDING',
        attributes: { command_preview: 'sleep 9999', tool_use_id: `tu-${sfx}`, is_test: true } },
    ])
    await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
      const resp = await route.fetch()
      const json = await resp.json()
      await route.fulfill({ response: resp, json: { ...json, ...phaseFields('inactive-stale') } })
    })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toBeVisible({ timeout: 10_000 })

    // Header reads inactive; the NOW zone is NOT ticking a tool (no spinner,
    // no 'tool' state) — the header-inactive + ticking-tool contradiction is
    // unreachable.
    await expect(page.locator('[data-testid="live-header"]')).toContainText('inactive')
    await expect(nowZone).not.toHaveAttribute('data-state', 'tool')
    await expect(nowZone.locator('.live-spinner')).toHaveCount(0)
    const dot = page.locator('.live-status-dot')
    await expect(dot).toHaveClass(/live-status-stale/)
    await expect(dot).not.toHaveClass(/live-status-running/)
  })
})

test.describe('Interrupted tool rows (Slice C)', () => {
  test('an is_interrupt tool span renders "⏹ interrupted" (and "by user" for a user abort), never a success verb; NOW never ticks it', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'interrupt-render fixture', is_test: true } },
      // Lost-ingest demotion: status ERROR + is_interrupt, interrupt_source stale.
      { trace_id: traceId, span_id: `stale-tu-${sfx}`, parent_id: null, name: 'tool.Bash',
        start_time: now, status_code: 'ERROR',
        attributes: { command_preview: 'pkill -f server', is_interrupt: true, interrupted: true,
          interrupt_source: 'stale', tool_use_id: `s-${sfx}`, is_test: true } },
      // The human interrupted execution: interrupt_source user.
      { trace_id: traceId, span_id: `user-tu-${sfx}`, parent_id: null, name: 'tool.Bash',
        start_time: now, status_code: 'ERROR',
        attributes: { command_preview: 'rm -rf build', is_interrupt: true, interrupted: true,
          interrupt_source: 'user', tool_use_id: `u-${sfx}`, is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })

    const staleRow = page.locator(`[data-testid="live-row"][data-span-id="stale-tu-${sfx}"]`)
    await expect(staleRow).toBeVisible()
    await expect(staleRow).toContainText('interrupted')
    await expect(staleRow).toContainText('⏹')
    await expect(staleRow).not.toContainText('by user')

    const userRow = page.locator(`[data-testid="live-row"][data-span-id="user-tu-${sfx}"]`)
    await expect(userRow).toContainText('interrupted by user')

    // ERROR (not PENDING) → the NOW zone never selects it as a live tool.
    await expect(page.locator('[data-testid="live-now"]')).not.toHaveAttribute('data-state', 'tool')
  })
})

test.describe('Queued / steer prompts (Slice C)', () => {
  test('server queued_prompts render, with a bridge steer reading "steering" and a plain one "queued"', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
      const resp = await route.fetch()
      const json = await resp.json()
      await route.fulfill({ response: resp, json: { ...json, queued_prompts: [
        { content: 'PLAIN_QUEUED_MARKER waiting turn' },
        { content: 'BRIDGE_STEER_MARKER mid-turn steer', source: 'bridge' },
      ] } })
    })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const queued = page.locator('[data-testid="live-queued"]')
    await expect(queued).toBeVisible({ timeout: 10_000 })
    const items = page.locator('[data-testid="live-queued-item"]')
    await expect(items.filter({ hasText: 'BRIDGE_STEER_MARKER' })).toContainText('steering')
    await expect(items.filter({ hasText: 'PLAIN_QUEUED_MARKER' })).toContainText('queued')
  })

  test('an optimistic steer chip appears after a bridge send and clears when the real prompt span lands', async ({ page }) => {
    const { traceId, sfx } = await postActiveSession(page)
    await bridgeReachableMap(page, traceId, { phase: 'idle' })
    await stubBridgeSend(page, traceId, { delivered: true, detail: 'delivered to %3' })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-now"]'))
      .toHaveAttribute('data-state', 'idle', { timeout: 20_000 })

    const STEER = 'OPTIMISTIC_STEER_MARKER run the flaky spec'
    await composerTa(page).fill(STEER)
    await composerSend(page).click()

    // The just-sent steer surfaces as an optimistic queued chip.
    const queued = page.locator('[data-testid="live-queued"]')
    await expect(queued).toContainText('OPTIMISTIC_STEER_MARKER', { timeout: 5_000 })

    // The real prompt span landing (same text) dedupes the optimistic chip away.
    await post(page, [
      { trace_id: traceId, span_id: `realprompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: new Date(Date.now() + 1000).toISOString(),
        attributes: { text: STEER, is_test: true } },
    ])
    await expect(page.locator('[data-testid="live-queued-item"]').filter({ hasText: 'OPTIMISTIC_STEER_MARKER' }))
      .toHaveCount(0, { timeout: 15_000 })
  })
})

test.describe('Scoped tail opens with the agent prompt (Slice C)', () => {
  async function openAgents(page) {
    await page.locator('[data-testid="live-agents-btn"]').click()
    await expect(page.locator('[data-testid="live-agent-sheet"]')).toBeVisible({ timeout: 5_000 })
  }

  test('a real prompt-sa span leads the scoped tail', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    const later = new Date(Date.now() + 2000).toISOString()
    const agId = `ag-${sfx}`
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'scoped-prompt fixture', is_test: true } },
      { trace_id: traceId, span_id: `agent-${sfx}`, parent_id: null, name: 'tool.Agent',
        start_time: now, attributes: { subagent_type: 'explorer', description: 'Map the code',
          tool_use_id: `tu-${sfx}`, agent_id: agId, is_test: true } },
      { trace_id: traceId, span_id: `substart-${sfx}`, parent_id: null, name: 'subagent.start',
        start_time: now, attributes: { agent_type: 'explorer', agent_id: agId, is_test: true } },
      { trace_id: traceId, span_id: `prompt-sa-${agId}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'SCOPED_PROMPT_MARKER — the task statement', agent_id: agId, is_test: true } },
      { trace_id: traceId, span_id: `int-read-${sfx}`, parent_id: null, name: 'tool.Read',
        start_time: later, attributes: { file_path: 'src/x.js', agent_id: agId, is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })

    await openAgents(page)
    await page.locator('[data-testid="live-agent-card"]').first().click()
    await expect(page.locator('[data-testid="live-scope-bar"]')).toBeVisible({ timeout: 5_000 })

    // The scoped tail's FIRST row is the agent's prompt (a message row).
    const first = rows(page).first()
    await expect(first).toContainText('SCOPED_PROMPT_MARKER')
    await expect(first).toHaveAttribute('data-kind', 'msg')
  })

  test('an old session (no prompt-sa) synthesizes the leading prompt from the launch description', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    const later = new Date(Date.now() + 2000).toISOString()
    const agId = `ag-${sfx}`
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'old-scoped fixture', is_test: true } },
      { trace_id: traceId, span_id: `agent-${sfx}`, parent_id: null, name: 'tool.Agent',
        start_time: now, attributes: { subagent_type: 'explorer', description: 'SYNTH_DESC_MARKER map the breakpoints',
          tool_use_id: `tu-${sfx}`, agent_id: agId, is_test: true } },
      { trace_id: traceId, span_id: `substart-${sfx}`, parent_id: null, name: 'subagent.start',
        start_time: now, attributes: { agent_type: 'explorer', agent_id: agId, is_test: true } },
      // Internal spans, but NO prompt-sa span (old session).
      { trace_id: traceId, span_id: `int-read-${sfx}`, parent_id: null, name: 'tool.Read',
        start_time: later, attributes: { file_path: 'src/y.js', agent_id: agId, is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })

    await openAgents(page)
    await page.locator('[data-testid="live-agent-card"]').first().click()
    await expect(page.locator('[data-testid="live-scope-bar"]')).toBeVisible({ timeout: 5_000 })

    const first = rows(page).first()
    await expect(first).toContainText('SYNTH_DESC_MARKER')
    await expect(first).toHaveAttribute('data-kind', 'msg')
  })

  test('a workflow agent scope leads with the marker prompt and closes with its result', async ({ page }) => {
    // Workflow-run markers carry the task prompt + result inline (no
    // prompt-sa span, no subagent.stop) — the scoped tail must not degrade
    // to the bare label and must surface the result at the end.
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    const later = new Date(Date.now() + 2000).toISOString()
    const agId = `ag-wf-${sfx}`
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'wf-run fixture', is_test: true } },
      { trace_id: traceId, span_id: `substart-${sfx}`, parent_id: null, name: 'subagent.start',
        start_time: now,
        attributes: { agent_type: 'workflow-subagent', agent_id: agId,
          label: 'survey:fixture', prompt: 'WF_LIVE_PROMPT_MARKER — dig into everything',
          result_preview: 'WF_LIVE_RESULT_MARKER — findings', state: 'done', is_test: true } },
      { trace_id: traceId, span_id: `int-read-${sfx}`, parent_id: null, name: 'tool.Read',
        start_time: later, attributes: { file_path: 'src/z.js', agent_id: agId, is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })

    await openAgents(page)
    // A state:done workflow agent rosters as finished — behind the toggle.
    const finished = page.locator('[data-testid="live-agent-finished-toggle"]')
    if (await finished.count()) await finished.click()
    await page.locator('[data-testid="live-agent-card"]').first().click()
    await expect(page.locator('[data-testid="live-scope-bar"]')).toBeVisible({ timeout: 5_000 })

    const first = rows(page).first()
    await expect(first).toContainText('WF_LIVE_PROMPT_MARKER')
    await expect(first).toHaveAttribute('data-kind', 'msg')
    await expect(rows(page).last()).toContainText('WF_LIVE_RESULT_MARKER')
  })
})

// ---- auto-page on scope entry (useLiveScope.autoPageScope) -----------------
//
// Roster span_count > 0 for an agent whose spans sit several older turn-pages
// back must not force a manual "load earlier history" tap: entering scope
// auto-pages via loadOlder() until the spans surface or history/the
// MAX_AUTO_PAGE_ITERS=20 backstop gives out (useLiveScope.js).

test.describe('Auto-page on scope entry (per-agent)', () => {
  // Only 'prompt' spans page the tail (lib/trace/trace_service/queries.py
  // _TURN_ANCHOR_NAMES), 5 anchors/page (useLiveTail's PAGE_SIZE) — 15 turns
  // spaced 30s apart puts the target agent's internal span in turn 0's
  // window, exactly 2 older pages (turns 10-14 -> 5-9 -> 0-4) behind the
  // initial mount, while keeping the whole span under the 10-min
  // agent-staleness threshold so the roster still reports it as running.
  async function seedFarAgent(page, { turnCount = 15, spacingMs = 30_000 } = {}) {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const agId = `ag-far-${sfx}`
    const baseMs = Date.now() - (turnCount - 1) * spacingMs
    const spans = []
    for (let i = 0; i < turnCount; i++) {
      spans.push({ trace_id: traceId, span_id: `prompt-${sfx}-${i}`, parent_id: null, name: 'prompt',
        start_time: new Date(baseMs + i * spacingMs).toISOString(),
        attributes: { text: `turn ${i} filler`, is_test: true } })
    }
    spans.push({ trace_id: traceId, span_id: `substart-${sfx}`, parent_id: null, name: 'subagent.start',
      start_time: new Date(baseMs + 5_000).toISOString(),
      attributes: { agent_type: 'digger', agent_id: agId, is_test: true } })
    spans.push({ trace_id: traceId, span_id: `int-read-${sfx}`, parent_id: null, name: 'tool.Read',
      start_time: new Date(baseMs + 10_000).toISOString(),
      attributes: { file_path: 'src/AUTOPAGE_INTERNAL_MARKER.js', agent_id: agId, is_test: true } })
    await post(page, spans)
    return { traceId, sfx, agId }
  }

  async function openAgents(page) {
    await page.locator('[data-testid="live-agents-btn"]').click()
    await expect(page.locator('[data-testid="live-agent-sheet"]')).toBeVisible({ timeout: 5_000 })
  }

  test('arrives at the agent\'s spans after a few loadOlder fetches, with no manual load tap', async ({ page }) => {
    const { traceId } = await seedFarAgent(page, { turnCount: 15, spacingMs: 30_000 })

    let beforeIdHits = 0
    // Delay each before_id (loadOlder) round trip so the transient
    // "live-scope-loading" state is observable instead of racing past it —
    // same delay-the-route convention as stubBridgeSend's delayMs above.
    await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
      if (route.request().url().includes('before_id=')) {
        beforeIdHits += 1
        await new Promise((r) => setTimeout(r, 250))
      }
      const resp = await route.fetch()
      await route.fulfill({ response: resp })
    })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })

    await openAgents(page)
    await page.locator('[data-testid="live-agent-card"]').first().click()
    await expect(page.locator('[data-testid="live-scope-bar"]')).toBeVisible({ timeout: 5_000 })

    // Auto-paging surfaces a loading state while it hunts for the spans...
    await expect(page.locator('[data-testid="live-scope-loading"]')).toBeVisible({ timeout: 5_000 })

    // ...and resolves to the real rows — no manual "load earlier history" tap,
    // and never the terminal empty state.
    await expect(page.locator('[data-testid="live-scope-load"]')).toHaveCount(0)
    await expect(rows(page).first()).toContainText('AUTOPAGE_INTERNAL_MARKER', { timeout: 10_000 })
    await expect(page.locator('[data-testid="live-scope-loading"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="live-scope-empty"]')).toHaveCount(0)

    expect(beforeIdHits, 'must exit as soon as the spans arrive, nowhere near the 20-iter cap')
      .toBeLessThan(20)
    expect(beforeIdHits, 'exactly 2 older turn-pages should reach turn 0 (5 anchors/page, 15 turns)')
      .toBe(2)
  })

  test('an over-claiming roster whose spans never arrive stops at the 20-iter cap, not indefinitely', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    const ghostAgentId = `ag-ghost-${sfx}`
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'auto-page storm fixture', is_test: true } },
    ])

    let beforeIdHits = 0
    await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
      const resp = await route.fetch()
      const json = await resp.json()
      if (route.request().url().includes('before_id=')) beforeIdHits += 1
      await route.fulfill({ response: resp, json: {
        ...json,
        // Over-claims forever: has_more_older never goes false, and the
        // ghost agent's spans never actually land in any page — the roster
        // knowing about spans that never arrive must not spin the loop.
        has_more_older: true,
        agent_roster: [{
          agent_id: ghostAgentId, agent_type: 'ghost', description: 'Never captured',
          status: 'stale', started_at: now, last_seen: now, duration_ms: null,
          result_preview: '', start_span_id: null, span_count: 10,
        }],
      } })
    })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })

    await openAgents(page)
    await page.locator('[data-testid="live-agent-finished-toggle"]').click()
    await page.locator('[data-testid="live-agent-finished"] [data-testid="live-agent-card"]')
      .first().click()

    const empty = page.locator('[data-testid="live-scope-empty"]')
    await expect(empty).toBeVisible({ timeout: 20_000 })
    await expect(empty).toContainText('spans not loaded — load earlier history to view')
    await expect(page.locator('[data-testid="live-scope-loading"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="live-scope-load"]')).toBeVisible()

    expect(beforeIdHits, 'the 20-iter cap must stop the loop, not spin forever').toBe(20)
  })
})

// ---- LiveAgentDetail sheet via the chevron ----------------------------------

test.describe('LiveAgentDetail sheet via chevron', () => {
  async function openAgents(page) {
    await page.locator('[data-testid="live-agents-btn"]').click()
    await expect(page.locator('[data-testid="live-agent-sheet"]')).toBeVisible({ timeout: 5_000 })
  }

  test('shows the roster prompt preview and start clock, sourced off the roster (not a loaded span)', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'agent-detail fixture', is_test: true } },
      { trace_id: traceId, span_id: `substart-${sfx}`, parent_id: null, name: 'subagent.start',
        start_time: now, attributes: { agent_type: 'explorer', agent_id: `ag-${sfx}`,
          prompt_preview: 'KNOWN_PROMPT_PREVIEW_MARKER — map the breakpoints', is_test: true } },
      { trace_id: traceId, span_id: `int-${sfx}`, parent_id: null, name: 'tool.Read',
        start_time: now, attributes: { file_path: 'src/x.js', agent_id: `ag-${sfx}`, is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })

    await openAgents(page)
    await page.locator('[data-testid="live-agent-info"]').first().click()

    const detail = page.locator('[data-testid="live-agent-detail"]')
    await expect(detail).toBeVisible({ timeout: 5_000 })
    await expect(detail).toContainText('KNOWN_PROMPT_PREVIEW_MARKER')
    await expect(detail).toContainText(/\d{2}:\d{2}/) // startClock — not the '—' placeholder
  })

  test('an empty prompt_preview falls back to "no prompt captured"; the chevron stays present', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'agent-detail empty-prompt fixture', is_test: true } },
      { trace_id: traceId, span_id: `substart-${sfx}`, parent_id: null, name: 'subagent.start',
        start_time: now, attributes: { agent_type: 'builder', agent_id: `ag-${sfx}`, is_test: true } },
      { trace_id: traceId, span_id: `int-${sfx}`, parent_id: null, name: 'tool.Read',
        start_time: now, attributes: { file_path: 'src/y.js', agent_id: `ag-${sfx}`, is_test: true } },
    ])

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-card"]')).toBeVisible({ timeout: 10_000 })

    await openAgents(page)
    const chevron = page.locator('[data-testid="live-agent-info"]').first()
    await expect(chevron).toBeVisible()
    await chevron.click()

    const detail = page.locator('[data-testid="live-agent-detail"]')
    await expect(detail).toBeVisible({ timeout: 5_000 })
    await expect(detail).toContainText('no prompt captured')
  })
})
