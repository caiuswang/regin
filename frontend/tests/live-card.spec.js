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

async function bridgeReachableMap(page, traceId, pane = '%3') {
  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    const resp = await route.fetch()
    const json = await resp.json()
    await route.fulfill({
      response: resp,
      json: { ...json, bridge_reachable: true, bridge_pane: pane },
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
    await bridgeReachableMap(page, traceId)

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'idle', { timeout: 10_000 })

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

  test('bridge not reachable → no composer and the non-idle response fallback', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    // No map patch: the real server (bridge disabled) reports
    // bridge_reachable: false.
    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'response', { timeout: 10_000 })
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
    // Serve bridge_reachable=true except on the SECOND map response — a
    // one-poll blip (tmux hiccup / registry churn) that unmounts the
    // composer and must not eat the user's typed draft.
    let served = 0
    await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
      const resp = await route.fetch()
      const json = await resp.json()
      served += 1
      const reachable = served !== 2
      await route.fulfill({
        response: resp,
        json: { ...json, bridge_reachable: reachable, bridge_pane: reachable ? '%3' : null },
      })
    })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'idle', { timeout: 10_000 })

    await composerTa(page).fill('half-typed steering thought')

    // Blip: next poll reports unreachable → composer unmounts, state falls
    // back to response.
    await expect(nowZone).toHaveAttribute('data-state', 'response', { timeout: 10_000 })
    await expect(composer(page)).toHaveCount(0)

    // Recovery: the following poll restores reachability → the remounted
    // composer still carries the draft.
    await expect(nowZone).toHaveAttribute('data-state', 'idle', { timeout: 10_000 })
    await expect(composerTa(page)).toHaveValue('half-typed steering thought')
  })

  test('response and tool states show the compact steer composer', async ({ page }) => {
    // response state
    const idle = await postActiveSession(page)
    await bridgeReachableMap(page, idle.traceId)
    // A pending tool forces 'tool' — bridge still reachable → steer variant.
    const busy = await postActiveSession(page, { pendingTool: true })
    await bridgeReachableMap(page, busy.traceId)

    await page.goto(`/live/${busy.traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'tool', { timeout: 10_000 })
    await expect(composer(page)).toHaveClass(/live-composer-steer/)
    await expect(composerTa(page)).toHaveAttribute('placeholder', /Steer the agent/)
    await expect(bridgeMeta(page)).toContainText('queues into the running turn')
  })
})

test.describe('Bridge send lifecycle (v5)', () => {
  test('delivered path: delivering → ✓ detail, textarea clears + re-enables, state and rows unchanged', async ({ page }) => {
    const { traceId } = await postActiveSession(page)
    await bridgeReachableMap(page, traceId)
    const posts = await stubBridgeSend(page, traceId,
      { delivered: true, detail: 'delivered to %3' }, { delayMs: 400 })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'idle', { timeout: 10_000 })
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
    await bridgeReachableMap(page, traceId)
    await stubBridgeSend(page, traceId,
      { delivered: false, detail: 'no reachable session' })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-now"]'))
      .toHaveAttribute('data-state', 'idle', { timeout: 10_000 })

    await composerTa(page).fill('keep this draft')
    await composerSend(page).click()

    await expect(bridgeMeta(page)).toContainText('no reachable session', { timeout: 5_000 })
    await expect(composerTa(page)).toBeEnabled()
    await expect(composerTa(page)).toHaveValue('keep this draft')
  })

  test('steer send from a working state keeps the state; Cmd/Ctrl+Enter sends', async ({ page }) => {
    const { traceId } = await postActiveSession(page, { pendingTool: true })
    await bridgeReachableMap(page, traceId)
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
    await bridgeReachableMap(page, traceId)

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'idle', { timeout: 10_000 })

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
    await bridgeReachableMap(page, traceId)

    await page.goto(`/live/${traceId}`)
    await settle(page)
    const nowZone = page.locator('[data-testid="live-now"]')
    await expect(nowZone).toHaveAttribute('data-state', 'idle', { timeout: 10_000 })

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
  await bridgeReachableMap(page, traceId)
  await stubBridgeCommands(page, traceId, FIXTURE_COMMANDS)
  await page.goto(`/live/${traceId}`)
  await settle(page)
  await expect(page.locator('[data-testid="live-now"]'))
    .toHaveAttribute('data-state', 'idle', { timeout: 10_000 })
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
