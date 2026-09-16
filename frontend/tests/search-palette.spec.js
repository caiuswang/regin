/**
 * Full-text search palette (⌘K).
 *
 * The invariants worth pinning are the ones a screenshot cannot prove: that
 * opening never disturbs the trace behind it, that a sub-2-char query issues
 * no request at all, that facet counts are computed BEFORE the facet filter
 * (so they sum to All), that ↵ opens exactly the row the keyboard was on —
 * including a span the client never paginated in — and that no query length
 * produces an empty <mark>.
 *
 * Scope and kind are two axes. Scope (This session / Everywhere) decides which
 * corpora are asked for at all; the facet chips pick a kind out of what came
 * back. ⌘F used to fuse them by pinning the Spans chip, so the tests below
 * assert it no longer opens anything.
 */
import { randomUUID } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { test, expect } from './auth-fixture.js'
import { selectViewMode, expectViewMode } from './helpers/viewMode.js'

const PANEL = '[role="dialog"][aria-label="Full-text search"]'
const ROW = `${PANEL} [role="option"]`
const INPUT = `${PANEL} .query-input`
const reader = page => page.getByTestId('span-present-reader')
// The reader's FOOTER names the previous/next span, so a marker assertion
// scoped to the whole dialog sees a neighbour's text in the nav chrome and
// proves nothing about which span is actually open.
const readerBody = page => page.locator('[data-present-body]')

// A term that exists nowhere else in the scratch DB, so every assertion is
// about spans this test seeded rather than whatever else the suite wrote.
const NEEDLE = 'zqxjrateLimit'
// A real file on disk that holds the needle, so the `file` corpus has
// something to rank without depending on what else the checkout contains.
const SELF_PATH = fileURLToPath(import.meta.url)

async function seed(page) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const prompt = `p-${p8}`
  const at = i => `2026-05-08T12:00:${String(i).padStart(2, '0')}`
  const spans = [{
    trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
    start_time: at(0),
    attributes: { text: `seeded prompt ${NEEDLE} MARKER_PROMPT`, is_test: true },
  }]
  let i = 1
  const add = (name, attributes) => {
    const id = `s${i}-${p8}`
    spans.push({
      trace_id: traceId, span_id: id, parent_id: prompt, name,
      start_time: at(i), duration_ms: 100,
      attributes: { ...attributes, is_test: true },
    })
    i++
    return id
  }
  // Each span carries a marker unique to IT, so an assertion can tell which
  // span the reader actually opened rather than merely that one did.
  // EVERY seeded span gets a marker, the prompt included: the ↵ test lands on
  // whichever row ranking puts at that index, and an unmapped row would fail
  // the test for a reason that is not the behaviour under test.
  const markers = { [prompt]: 'MARKER_PROMPT' }
  // The diff is the ONLY place the needle appears for this span — the
  // acceptance item is that a term living in a diff surfaces its edit span.
  markers[add('tool.Edit', {
    tool_name: 'Edit', file_path: '/repo/router.py', additions: 2, deletions: 1,
    diff: `@@ -1,3 +1,4 @@\n-old = 1\n+${NEEDLE} = 5\n keep = 3`,
  })] = `${NEEDLE} = 5`
  markers[add('tool.Bash', {
    tool_name: 'Bash', command: `grep ${NEEDLE} .`,
    stdout: `${NEEDLE} found in MARKER_BASH\n${NEEDLE} again`,
  })] = 'MARKER_BASH'
  markers[add('assistant_response', {
    text: `The ${NEEDLE} guard sits in MARKER_ASSISTANT.`,
  })] = 'MARKER_ASSISTANT'
  markers[add('rule.check', {
    file_path: '/repo/hooks.py',
    applicable_rules: [
      { id: 'MARKER_RULE', violated: false, summary: `the ${NEEDLE} guard is checked` },
      { id: 'no-raw-sqlite', violated: true, summary: 'raw sqlite3 outside the ORM', match_count: 2 },
    ],
  })] = 'MARKER_RULE'
  markers[add('tool.AskUserQuestion', {
    tool_name: 'AskUserQuestion',
    description: `ask about ${NEEDLE}`,
    questions: [{
      header: 'Guard',
      question: 'Which guard should MARKER_ASK use?',
      options: [{ label: 'retry', description: 'back off and retry' }, { label: 'fail' }],
    }],
    answers: { 'Which guard should MARKER_ASK use?': 'retry' },
  })] = 'MARKER_ASK'
  // Its needle lives ONLY in `description`, a field spanBody indexes and
  // presentFor never renders.
  markers[add('tool.Task', {
    tool_name: 'Task', description: `dispatch the ${NEEDLE} audit MARKER_TASK`,
  })] = 'MARKER_TASK'
  add('tool.Read', { tool_name: 'Read', file_path: '/repo/unrelated.py' })
  // Carries no needle in its own body — it is here to put a real path into the
  // touched-files corpus, not to rank as a span row.
  add('tool.MultiEdit', { tool_name: 'MultiEdit', file_path: SELF_PATH, additions: 3, deletions: 1 })

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  return { traceId, markers }
}

const SCOPE = label => `${PANEL} .scope:has-text("${label}")`

// `facet` pins the kind chip a test needs; the palette itself defaults to All.
async function openPalette(page, facet = null) {
  await page.keyboard.press('Meta+k')
  await page.waitForSelector(PANEL)
  if (facet) await page.locator(`${PANEL} .facet`, { hasText: facet }).first().click()
}

async function setScope(page, label) {
  await page.locator(SCOPE(label)).click()
  await expect(page.locator(SCOPE(label))).toHaveAttribute('aria-pressed', 'true')
}

async function search(page, text) {
  await page.locator(INPUT).fill(text)
  // The remote leg is debounced 140ms; give it the round trip.
  await page.waitForTimeout(1500)
}

test.describe('full-text search palette', () => {
  test('opens focused without disturbing the trace behind it', async ({ page }) => {
    await seed(page)
    const before = await page.evaluate(
      () => document.querySelector('.content-scroll')?.scrollTop ?? 0)
    await openPalette(page)
    await expect(page.locator(INPUT)).toBeFocused()
    expect(await page.evaluate(
      () => document.querySelector('.content-scroll')?.scrollTop ?? 0)).toBe(before)
  })

  test('esc closes and keeps the query for the next open', async ({ page }) => {
    await seed(page)
    await openPalette(page)
    await search(page, NEEDLE)
    await page.keyboard.press('Escape')
    await expect(page.locator(PANEL)).toHaveCount(0)
    await openPalette(page)
    await expect(page.locator(INPUT)).toHaveValue(NEEDLE)
  })

  test('a query under two characters issues no request and no rows', async ({ page }) => {
    const calls = []
    await page.route('**/api/search**', route => {
      calls.push(route.request().url())
      return route.continue()
    })
    await seed(page)
    await openPalette(page)
    await page.locator(INPUT).fill('a')
    await page.waitForTimeout(800)
    expect(calls).toHaveLength(0)
    await expect(page.locator(ROW)).toHaveCount(0)
  })

  test('a term that exists only in a diff surfaces its edit span', async ({ page }) => {
    await seed(page)
    await openPalette(page, 'Spans')
    await search(page, NEEDLE)
    const edit = page.locator(ROW).filter({ hasText: 'tool.Edit' }).first()
    await expect(edit).toBeVisible()
    await expect(edit.locator('.row-hits')).toHaveText('1 match')
    await edit.click()
    // The preview renders the edit through the trace's own diff block, with
    // the term marked inside the added line.
    const diff = page.locator(`${PANEL} .present-diff`)
    await expect(diff).toContainText(`+${NEEDLE} = 5`)
    await expect(diff.locator('mark').first()).toHaveText(NEEDLE)
  })

  test('a span hit previews through the trace’s own renderers', async ({ page }) => {
    await seed(page)
    await openPalette(page, 'Spans')
    await search(page, NEEDLE)
    const preview = page.locator(`${PANEL} .preview-body`)
    const row = name => page.locator(ROW).filter({ hasText: name }).first()

    // A rule check is a list of rules, not a paragraph of flattened text.
    await row('rule.check').click()
    await expect(preview.locator('.present-rule')).toHaveCount(2)
    // A span already in the client model is rendered outright — no skeleton.
    await expect(preview.locator('.span-hydrating')).toHaveCount(0)
    await expect(preview.locator('.present-rule-mark').first()).toHaveText('✓')
    await expect(preview.locator('.present-rule-summary mark').first()).toHaveText(NEEDLE)

    // A shell span keeps its command line and its output block.
    await row('tool.Bash').click()
    await expect(preview.locator('.present-term-cmd')).toContainText(`grep ${NEEDLE}`)
    await expect(preview.locator('.present-term-out')).toContainText('MARKER_BASH')
    await expect(preview.locator('.present-term-out mark').first()).toHaveText(NEEDLE)

    // Prose renders as markdown, marked in place.
    await row('assistant_response').click()
    await expect(preview.locator('.present-prose mark').first()).toHaveText(NEEDLE)

    // An ask renders the Q&A round trip.
    await row('AskUserQuestion').click()
    await expect(preview.locator('.present-ask-question')).toContainText('MARKER_ASK')
    await expect(preview.locator('.present-ask-opt.is-chosen')).toContainText('retry')
  })

  test('a span the client never paginated in hydrates into the preview', async ({ page }) => {
    // Withhold the shell span from the trace loads until the palette is up:
    // the client model then cannot hold it while the server-side search still
    // finds it, which is the shape of every hit outside the loaded window.
    // Flipped by the test rather than by a delay, so the ordering is exact.
    let reachable = false
    const withhold = async (route) => {
      const res = await route.fetch()
      const body = await res.json()
      if (!reachable && Array.isArray(body.spans)) {
        body.spans = body.spans.filter(s => s.name !== 'tool.Bash')
      }
      await route.fulfill({ response: res, json: body })
    }
    await page.route('**/map**', withhold)
    await page.route('**/children**', withhold)
    const subtreeCalls = []
    page.on('request', r => { if (r.url().includes('/children')) subtreeCalls.push(r.url()) })
    await seed(page)
    await openPalette(page, 'Spans')
    await search(page, NEEDLE)
    const before = subtreeCalls.length
    reachable = true
    await page.locator(ROW).filter({ hasText: 'tool.Bash' }).first().click()
    // The rich render is the proof: the fallback line preview carries the same
    // text but never a `.present-term-cmd`.
    await expect(page.locator(`${PANEL} .present-term-cmd`)).toContainText(`grep ${NEEDLE}`)
    expect(subtreeCalls.length).toBeGreaterThan(before)
  })

  test('previewing a remote span leaves the trace exactly where it was', async ({ page }) => {
    // The palette is a reader, not a navigator (spec §01): closing it must
    // leave the trace untouched. Hydrating an out-of-window span used to run
    // the full `selectSpanById`, which switched the tab (and PERSISTED it),
    // moved the selection and opened the detail rail — all behind the overlay.
    let reachable = false
    const withhold = async (route) => {
      const res = await route.fetch()
      const body = await res.json()
      if (!reachable && Array.isArray(body.spans)) {
        body.spans = body.spans.filter(s => s.name !== 'tool.Bash')
      }
      await route.fulfill({ response: res, json: body })
    }
    await page.route('**/map**', withhold)
    await page.route('**/children**', withhold)
    await seed(page)

    // Park the view somewhere a stray setViewMode would visibly move it off.
    await selectViewMode(page, 'Log')
    await expectViewMode(page, 'Log')
    const modeBefore = await page.evaluate(
      () => localStorage.getItem('regin_session_view_mode'))
    expect(modeBefore).toBe('log')

    await openPalette(page, 'Spans')
    await search(page, NEEDLE)
    reachable = true
    await page.locator(ROW).filter({ hasText: 'tool.Bash' }).first().click()
    // The hydrate really did happen — otherwise this asserts nothing.
    await expect(page.locator(`${PANEL} .present-term-cmd`)).toContainText(`grep ${NEEDLE}`)

    await page.keyboard.press('Escape')
    await expect(page.locator(PANEL)).toHaveCount(0)
    await expectViewMode(page, 'Log')
    expect(await page.evaluate(
      () => localStorage.getItem('regin_session_view_mode'))).toBe(modeBefore)
  })

  test('a span matching only in an unrendered field still shows its match', async ({ page }) => {
    // `spanBody` indexes description/pattern/rule_file/file_path; `presentFor`
    // renders none of them. Such a span has a real hit and an empty view
    // model, so the rich renderer would say "no body — metadata only" right
    // under a badge counting matches.
    await seed(page)
    await openPalette(page, 'Spans')
    await search(page, NEEDLE)
    const row = page.locator(ROW).filter({ hasText: 'tool.Task' }).first()
    await expect(row).toBeVisible()
    await expect(row.locator('.row-hits')).toContainText('match')
    await row.click()
    const body = page.locator(`${PANEL} .preview-body`)
    await expect(body).not.toContainText('metadata only')
    await expect(body.locator('mark').first()).toHaveText(NEEDLE)
  })

  test('sessions keep the plain line preview', async ({ page }) => {
    await seed(page)
    await openPalette(page)
    // Other sessions are an Everywhere answer — inside one session they are
    // not a kind that can have results.
    await setScope(page, 'Everywhere')
    await search(page, NEEDLE)
    const rows = page.locator(`${PANEL} [role="group"][aria-label="Sessions"] [role="option"]`)
    if (!(await rows.count())) test.skip(true, 'no other sessions in this environment')
    await rows.first().click()
    await expect(page.locator(`${PANEL} .preview-line`).first()).toBeVisible()
    await expect(page.locator(`${PANEL} .present-content`)).toHaveCount(0)
  })

  test('a file hit renders through the diff block', async ({ page }) => {
    await seed(page)
    await openPalette(page)
    await search(page, NEEDLE)
    const rows = page.locator(`${PANEL} [role="group"][aria-label="Files & diffs"] [role="option"]`)
    if (!(await rows.count())) test.skip(true, 'no file corpus in this environment')
    await rows.first().click()
    await expect(page.locator(`${PANEL} .preview-diff .diff-block`)).toBeVisible()
    await expect(page.locator(`${PANEL} .preview-diff mark`).first()).toHaveText(NEEDLE)
  })

  test('patterns keep the plain line preview', async ({ page }) => {
    await seed(page)
    await openPalette(page)
    await setScope(page, 'Everywhere')
    await search(page, 'pattern')
    const row = page.locator(`${PANEL} [role="group"][aria-label="Patterns & wiki"] [role="option"]`)
    if (!(await row.count())) test.skip(true, 'no pattern corpus in this environment')
    await row.first().click()
    await expect(page.locator(`${PANEL} .preview-line`).first()).toBeVisible()
    await expect(page.locator(`${PANEL} .present-content`)).toHaveCount(0)
  })

  test('facet counts sum to the All count', async ({ page }) => {
    await seed(page)
    await openPalette(page)
    await search(page, NEEDLE)
    const counts = (await page.locator(`${PANEL} .facet .facet-count`).allTextContents())
      .map(Number)
    const [all, ...rest] = counts
    expect(rest.reduce((a, b) => a + b, 0)).toBe(all)
  })

  test('tab cycles the kind chips within the scope', async ({ page }) => {
    await seed(page)
    await openPalette(page)
    await expect(page.locator(`${PANEL} .facet.is-active`)).toContainText('All')
    await page.keyboard.press('Tab')
    await expect(page.locator(`${PANEL} .facet.is-active`)).toContainText('Spans')
    // In-session scope offers exactly All / Spans / Files, so a third Tab
    // wraps rather than landing on a kind this scope cannot answer.
    await page.keyboard.press('Tab')
    await expect(page.locator(`${PANEL} .facet.is-active`)).toContainText('Files')
    await page.keyboard.press('Tab')
    await expect(page.locator(`${PANEL} .facet.is-active`)).toContainText('All')
  })

  test('⌘F no longer opens anything', async ({ page }) => {
    await seed(page)
    await page.keyboard.press('Meta+f')
    await page.waitForTimeout(400)
    await expect(page.locator(PANEL)).toHaveCount(0)
    // ...and the one chord that remains still works from the same place.
    await openPalette(page)
    await expect(page.locator(PANEL)).toHaveCount(1)
  })

  test('scope decides which corpora are asked for', async ({ page }) => {
    const urls = []
    await page.route('**/api/search**', (route) => {
      urls.push(route.request().url())
      return route.continue()
    })
    await seed(page)
    await openPalette(page)
    await expect(page.locator(SCOPE('This session'))).toHaveAttribute('aria-pressed', 'true')
    await search(page, NEEDLE)
    const inSession = urls.at(-1)
    expect(inSession).toContain('kinds=span%2Cfile')
    expect(inSession).toContain('session=')

    await setScope(page, 'Everywhere')
    await page.waitForTimeout(1500)
    const everywhere = urls.at(-1)
    expect(everywhere).toContain('kinds=pattern%2Csession%2Cfile')
    expect(everywhere).toContain('scope=global')
    // The trace id rides along under BOTH scopes — under `global` it is the
    // session to leave out of "other sessions", not a corpus to search.
    expect(everywhere).toContain('session=')
  })

  test('a slow session-scoped answer cannot land on top of a global one',
    async ({ page }) => {
      // Scope changes the corpora without touching the query, so an in-flight
      // token keyed on the query alone could not tell the two apart: the late
      // session answer overwrote the global one, dropping the Patterns and
      // Sessions groups and leaving session-scoped Files under "Everywhere".
      let release
      const held = new Promise((resolve) => { release = resolve })
      await page.route('**/api/search**', async (route) => {
        if (route.request().url().includes('scope=session')) await held
        return route.continue()
      })
      // TWO sessions, so "Everywhere" has an answer that session scope cannot
      // produce — the Sessions group is what the stale answer destroys, and
      // with only one session in the DB both answers are empty and the
      // regression is invisible.
      await seed(page)
      await seed(page)
      await openPalette(page)
      await page.locator(INPUT).fill(NEEDLE)
      await page.waitForTimeout(400)
      await setScope(page, 'Everywhere')
      const sessions = page.locator(
        `${PANEL} #search-listbox [role="group"][aria-label="Sessions"] [role="option"]`)
      await expect(sessions.first()).toBeVisible({ timeout: 10_000 })
      const before = await sessions.count()

      release()
      await page.waitForTimeout(1800)
      // The Everywhere answer must still be standing. Verified to fail when
      // the in-flight token is the query alone.
      expect(await sessions.count()).toBe(before)
      await expect(page.locator(SCOPE('Everywhere'))).toHaveAttribute('aria-pressed', 'true')
    })

  test('ctrl+s toggles the scope from the keyboard', async ({ page }) => {
    await seed(page)
    await openPalette(page)
    await page.keyboard.press('Control+s')
    await expect(page.locator(SCOPE('Everywhere'))).toHaveAttribute('aria-pressed', 'true')
    await page.keyboard.press('Control+s')
    await expect(page.locator(SCOPE('This session'))).toHaveAttribute('aria-pressed', 'true')
  })

  test('with no trace open the in-session scope is disabled, not merely empty',
    async ({ page }) => {
      await page.goto('/inbox')
      await page.locator('body').click()
      await openPalette(page)
      await expect(page.locator(SCOPE('This session'))).toBeDisabled()
      await expect(page.locator(SCOPE('Everywhere'))).toHaveAttribute('aria-pressed', 'true')
      // The kind chips follow: a Spans chip here could only ever read 0.
      await expect(page.locator(`${PANEL} .facet`)).toHaveText([
        /All/, /Patterns/, /Sessions/, /Files/,
      ])
    })

  test('an empty in-session result offers to widen, and widening switches scope',
    async ({ page }) => {
      await seed(page)
      await openPalette(page)
      // Assembled at runtime: the `file` corpus greps every path the session
      // TOUCHED, and this spec is one of them — a literal here would match
      // itself, so the empty state could never be reached.
      await search(page, ['zzqq', 'xxnowhere'].join(''))
      await expect(page.locator(`${PANEL} .no-matches`)).toBeVisible()
      await page.locator(`${PANEL} .no-matches-widen`).click()
      await expect(page.locator(SCOPE('Everywhere'))).toHaveAttribute('aria-pressed', 'true')
    })

  test('on a narrow viewport a tap on a row opens the preview sheet', async ({ page }) => {
    // Below NARROW_MAX_PX the preview is a push-over sheet, and ↵ used to be
    // the only way into it — a phone tap selected a row and showed nothing.
    await page.setViewportSize({ width: 480, height: 900 })
    await seed(page)
    await openPalette(page, 'Spans')
    await search(page, NEEDLE)
    await expect(page.locator(`${PANEL} .search-preview`)).toBeHidden()
    await page.locator(ROW).first().click()
    await expect(page.locator(`${PANEL} .search-preview`)).toBeVisible()
    // ...and the sheet's own back affordance returns to the list.
    await page.locator(`${PANEL} .sheet-back button`).click()
    await expect(page.locator(`${PANEL} .search-results`)).toBeVisible()
  })

  test('the header count agrees with the active kind chip', async ({ page }) => {
    await seed(page)
    await openPalette(page, 'Spans')
    await search(page, NEEDLE)
    const chip = await page.locator(`${PANEL} .facet.is-active .facet-count`).textContent()
    await expect(page.locator(`${PANEL} .query-count`))
      .toHaveText(new RegExp(`^${Number(chip)} results?$`))
  })

  test('arrowing down then ↵ opens exactly the highlighted row', async ({ page }) => {
    const { markers } = await seed(page)
    await openPalette(page, 'Spans')
    await search(page, NEEDLE)
    expect(await page.locator(ROW).count()).toBeGreaterThan(2)
    for (let n = 0; n < 2; n += 1) await page.keyboard.press('ArrowDown')
    const activeId = await page.locator(`${PANEL} [aria-selected="true"]`)
      .getAttribute('id')
    const spanId = activeId.replace('search-row-', '')
    const marker = markers[spanId]
    expect(marker, `row ${spanId} should be one of the seeded spans`).toBeTruthy()

    await page.keyboard.press('Enter')
    await expect(page.locator(PANEL)).toHaveCount(0)
    // The reader is showing THAT span's body, not a neighbour's — the marker
    // exists on exactly one seeded span.
    await expect(readerBody(page)).toContainText(marker)
    // The reader is a reading surface: the palette's highlight does not follow
    // the span into it.
    await expect(readerBody(page).locator('mark')).toHaveCount(0)
    for (const [otherId, otherMarker] of Object.entries(markers)) {
      if (otherId !== spanId) {
        await expect(readerBody(page)).not.toContainText(otherMarker)
      }
    }
  })

  test('the palette opens over presentation mode and esc returns to it', async ({ page }) => {
    const { markers } = await seed(page)
    const [someSpanId] = Object.keys(markers)
    // Open the reader first, then the palette on top of it.
    await openPalette(page, 'Spans')
    await search(page, NEEDLE)
    await page.keyboard.press('Enter')
    await expect(reader(page)).toBeVisible()

    // The reader traps focus and owns Escape. The palette must still be
    // reachable by keyboard from inside it...
    await page.keyboard.press('Meta+k')
    await page.waitForSelector(PANEL)
    await expect(page.locator(INPUT)).toBeFocused()
    await page.keyboard.type('abc')
    await expect(page.locator(INPUT)).toHaveValue(/abc/)

    // ...and Escape must retire the PALETTE, leaving the reader standing.
    await page.keyboard.press('Escape')
    await expect(page.locator(PANEL)).toHaveCount(0)
    await expect(reader(page)).toBeVisible()
    expect(someSpanId).toBeTruthy()
  })

  test('never renders an empty highlight, at any query length', async ({ page }) => {
    await seed(page)
    await openPalette(page)
    for (const q of NEEDLE.split('').map((_, n) => NEEDLE.slice(0, n + 1))) {
      await page.locator(INPUT).fill(q)
      await page.waitForTimeout(250)
      const empty = await page.locator(`${PANEL} mark`).evaluateAll(
        els => els.filter(e => e.textContent.length === 0).length)
      expect(empty, `empty <mark> for query "${q}"`).toBe(0)
    }
  })

  test('a bare / opens the palette on a feed view but never over a dialog', async ({ page }) => {
    await seed(page)
    // Feed view, no caret: `/` is the search affordance.
    await page.locator('body').click()
    await page.keyboard.press('/')
    await page.waitForSelector(PANEL)
    // `/` opens the palette as it stands — it no longer pins a kind, which on
    // a feed with no trace behind it could only pin an empty one.
    await expect(page.locator(`${PANEL} .facet.is-active`)).toContainText('All')
    await page.keyboard.press('Escape')

    // Inside the reader it is not: presentation mode documents `/` as a key
    // it must swallow, and only ⌘K pierces (spec §07).
    await openPalette(page, 'Spans')
    await search(page, NEEDLE)
    await page.keyboard.press('Enter')
    await expect(reader(page)).toBeVisible()
    await page.keyboard.press('/')
    await expect(page.locator(PANEL)).toHaveCount(0)
    await expect(reader(page)).toBeVisible()
  })

  test('groups render in the fixed kind order', async ({ page }) => {
    await seed(page)
    await openPalette(page)
    await search(page, NEEDLE)
    const labels = await page.locator(`${PANEL} #search-listbox [role="group"]`)
      .evaluateAll(els => els.map(e => e.getAttribute('aria-label')))
    const ORDER = ['This session · spans', 'Patterns & wiki', 'Sessions', 'Files & diffs']
    expect(labels).toEqual(ORDER.filter(l => labels.includes(l)))
    // The order holds across the scope switch too, over the other kind set.
    await setScope(page, 'Everywhere')
    await page.waitForTimeout(1500)
    const wide = await page.locator(`${PANEL} #search-listbox [role="group"]`)
      .evaluateAll(els => els.map(e => e.getAttribute('aria-label')))
    expect(wide).toEqual(ORDER.filter(l => wide.includes(l)))
    expect(wide).not.toContain('This session · spans')
  })

  test('a remote group failing leaves the local span results usable', async ({ page }) => {
    await seed(page)
    await page.route('**/api/search**', route => route.abort())
    await openPalette(page, 'Spans')
    await search(page, NEEDLE)
    // The local pass answered from spans already in the client model...
    await expect(page.locator(ROW).first()).toBeVisible()
    // ...and the group says so, rather than passing a partial answer off as
    // the whole session.
    await expect(page.locator(`${PANEL} .group-retry`).first())
      .toHaveText(/showing loaded spans only/)
  })
})
