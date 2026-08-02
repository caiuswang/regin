/**
 * `/live` composer autocomplete — `@` file mentions, plus the menu affordances
 * the two trigger modes share (loading row, argument hints, destructive
 * badge). Companion to the `/`-only block in live-card.spec.js; same 375px
 * viewport, same DOM contract (live-command-menu / live-command-item /
 * live-command-empty) with three additions: live-command-loading,
 * live-command-risk, and the `.live-cmd-icon` file/directory glyph.
 *
 * Both catalog endpoints are stubbed with deterministic fixtures — the backend
 * half (`bridge-commands` / `bridge-files`) is out of this file's scope; what's
 * under test is the browser-side trigger grammar, the accept text, and the
 * caret it leaves behind.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'
import { settle } from './helpers/overflow.js'

test.use({ viewport: { width: 375, height: 667 } })

// ---- console-error gate ----------------------------------------------------

const errorsFor = new Map()

test.beforeEach(async ({ page }) => {
  const errs = []
  errorsFor.set(page, errs)
  page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()) })
  page.on('pageerror', (e) => errs.push(`pageerror: ${e}`))
})

test.afterEach(async ({ page }) => {
  const errs = errorsFor.get(page) || []
  errorsFor.delete(page)
  expect(errs, `console errors: ${errs.join(' | ')}`).toEqual([])
})

// ---- fixtures --------------------------------------------------------------

const FIXTURE_COMMANDS = [
  { name: 'deploy', description: 'Ship the current branch to prod.', argumentHint: '<branch>',
    kind: 'command', scope: 'project', risk: null },
  { name: 'lint', description: 'Lint the tree.', kind: 'skill', scope: 'project', risk: null },
  { name: 'clear', description: 'Clear conversation history.', kind: 'builtin', scope: 'builtin',
    risk: 'destructive', aliases: ['reset', 'new'] },
  // A row the server sent without a `kind` — the menu must not badge it with an
  // empty pill. It also carries no `aliases` key at all, the shape an older
  // cached response has.
  { name: 'kindless', description: 'No kind on this row.' },
  { name: 'loop', description: 'Keep working the queue.', kind: 'builtin', scope: 'builtin',
    aliases: ['proactive'] },
  // Matches "reset" in its DESCRIPTION only — the foil for alias ranking.
  { name: 'restore', description: 'Undo a reset of the working tree.', kind: 'command',
    scope: 'project', aliases: [] },
]

// Lucide `file` / `folder` first-path geometry, pinned so the file rows can't
// silently regress to a repurposed glyph.
const FILE_GLYPH = 'M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z'
const FOLDER_GLYPH = 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z'

const LONG_FILE = 'src/components/live/an-extremely-long-component-file-name-for-overflow.vue'
const FIXTURE_FILES = [
  { path: 'README.md', kind: 'file' },
  { path: 'src', kind: 'directory' },
  { path: 'src/app.js', kind: 'file' },
  { path: 'src/lib', kind: 'directory' },
  { path: LONG_FILE, kind: 'file' },
]

async function post(page, spans) {
  const res = await page.request.post('/api/session-spans', { data: spans })
  expect(res.ok()).toBeTruthy()
}

function quietLastSeen() {
  return new Date(Date.now() - 30_000).toISOString()
}

async function bridgeReachableMap(page, traceId) {
  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    const resp = await route.fetch()
    const json = await resp.json()
    await route.fulfill({
      response: resp,
      json: {
        ...json,
        bridge_reachable: true,
        bridge_pane: '%3',
        last_seen: quietLastSeen(),
        phase: 'idle',
        agent_phase: { main: 'idle' },
        phase_config: { working_window_sec: 12, idle_settle_sec: 6, inactive_threshold_sec: 600 },
      },
    })
  })
}

// Returns the collected request URLs so a test can assert how many times the
// catalog was actually fetched.
async function stubBridgeCommands(page, traceId, commands, { delayMs = 0 } = {}) {
  const requests = []
  await page.route(`**/api/sessions/${traceId}/bridge-commands`, async (route) => {
    requests.push(route.request().url())
    if (delayMs) await new Promise((r) => setTimeout(r, delayMs))
    await route.fulfill({ json: { commands } })
  })
  return requests
}

// Serves a different `commands` payload per call, so a test can drive the
// degraded-then-recovered catalog the backend produces while its own 30s
// failure cache is warm.
async function stubBridgeCommandsSeq(page, traceId, payloads) {
  const requests = []
  await page.route(`**/api/sessions/${traceId}/bridge-commands`, async (route) => {
    const commands = payloads[Math.min(requests.length, payloads.length - 1)]
    requests.push(route.request().url())
    await route.fulfill({ json: { commands } })
  })
  return requests
}

// Stubs the file search; returns the collected `q` values so a test can assert
// the debounce/staleness behaviour if it needs to. `delayFor(q)` gives a
// per-query latency (for out-of-order responses); `delayMs` is the fallback.
async function stubBridgeFiles(
  page, traceId, { delayMs = 0, delayFor = null, files = FIXTURE_FILES } = {}) {
  const queries = []
  await page.route(`**/api/sessions/${traceId}/bridge-files*`, async (route) => {
    const q = (new URL(route.request().url()).searchParams.get('q') || '').toLowerCase()
    queries.push(q)
    const wait = delayFor ? delayFor(q) : delayMs
    if (wait) await new Promise((r) => setTimeout(r, wait))
    const hits = q ? files.filter((f) => f.path.toLowerCase().includes(q)) : files
    await route.fulfill({ json: { files: hits } })
  })
  return queries
}

async function stubBridgeSend(page, traceId, result) {
  const posts = []
  await page.route(`**/api/sessions/${traceId}/bridge-send`, async (route) => {
    posts.push(route.request().postDataJSON())
    await route.fulfill({ json: { id: 1, ...result } })
  })
  return posts
}

async function postActiveSession(page) {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const now = new Date().toISOString()
  await post(page, [
    { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
      start_time: now, attributes: { text: 'mention fixture prompt', is_test: true } },
    { trace_id: traceId, span_id: `resp-${sfx}`, parent_id: null, name: 'assistant_response',
      start_time: now, attributes: { text: 'IDLE_RESPONSE_MARKER', is_test: true } },
  ])
  return { traceId, sfx }
}

const composerTa = (page) => page.locator('[data-testid="live-composer-ta"]')
const bridgeMeta = (page) => page.locator('[data-testid="live-bridge-meta"]')
const cmdMenu = (page) => page.locator('[data-testid="live-command-menu"]')
const cmdItems = (page) => page.locator('[data-testid="live-command-item"]')
const cmdLoading = (page) => page.locator('[data-testid="live-command-loading"]')
const cmdEmpty = (page) => page.locator('[data-testid="live-command-empty"]')

// Render the idle bridge composer with both catalogs stubbed.
async function idleComposer(
  page, { commandDelayMs = 0, fileDelayMs = 0, fileDelayFor = null } = {}) {
  const { traceId } = await postActiveSession(page)
  await bridgeReachableMap(page, traceId)
  const commandRequests = await stubBridgeCommands(
    page, traceId, FIXTURE_COMMANDS, { delayMs: commandDelayMs })
  const queries = await stubBridgeFiles(
    page, traceId, { delayMs: fileDelayMs, delayFor: fileDelayFor })
  await page.goto(`/live/${traceId}`)
  await settle(page)
  await expect(page.locator('[data-testid="live-now"]'))
    .toHaveAttribute('data-state', 'idle', { timeout: 20_000 })
  return { traceId, queries, commandRequests }
}

const caretOf = (page) => composerTa(page).evaluate((el) => el.selectionStart)

// The composer's combobox wiring plus whether each id it advertises actually
// resolves to an element — a dangling aria-controls / aria-activedescendant is
// worse than none.
const ariaState = (page) => composerTa(page).evaluate((el) => {
  const controls = el.getAttribute('aria-controls')
  const active = el.getAttribute('aria-activedescendant')
  const count = (id) => (id ? document.querySelectorAll(`#${CSS.escape(id)}`).length : 0)
  return {
    controls,
    active,
    expanded: el.getAttribute('aria-expanded'),
    controlsResolves: count(controls),
    activeResolves: count(active),
    activeIsHighlighted: !!active
      && document.querySelector('[data-highlighted="true"]')?.id === active,
  }
})

// ---- trigger grammar -------------------------------------------------------

test.describe('@-mention trigger (raw-terminal parity)', () => {
  test('"@" at index 0 opens the menu on the project root', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('@')
    await expect(cmdMenu(page)).toBeVisible({ timeout: 5_000 })
    await expect(cmdItems(page)).toHaveCount(FIXTURE_FILES.length, { timeout: 5_000 })
    await expect(cmdItems(page).nth(0)).toContainText('@README.md')
    await expect(cmdItems(page).nth(1)).toContainText('@src')
  })

  test('"@" after whitespace opens and narrows to the query', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('look at @src')
    await expect(cmdMenu(page)).toBeVisible({ timeout: 5_000 })
    await expect(cmdItems(page)).toHaveCount(4, { timeout: 5_000 }) // src, src/app.js, src/lib, long
    await expect(cmdItems(page).nth(0)).toContainText('@src')
  })

  test('"@" inside an email-like token never fires', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('mail foo@bar.com')
    await page.waitForTimeout(500) // past the 180ms debounce
    await expect(cmdMenu(page)).toHaveCount(0)
  })

  test('"@" straight after a quote never fires', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('open "@src')
    await page.waitForTimeout(500)
    await expect(cmdMenu(page)).toHaveCount(0)
  })

  test('"@@" never opens a popup on the literal "@" query', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).pressSequentially('@@')
    await page.waitForTimeout(500) // past the 180ms debounce
    await expect(cmdMenu(page)).toHaveCount(0)
  })

  test('the menu closes once the caret runs past a space', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('@src')
    await expect(cmdMenu(page)).toBeVisible({ timeout: 5_000 })
    await composerTa(page).fill('@src ')
    await expect(cmdMenu(page)).toHaveCount(0)
  })
})

// ---- accept ----------------------------------------------------------------

test.describe('@-mention accept', () => {
  test('accepting a file inserts "@path " and parks the caret right after it', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('@READ')
    await expect(cmdItems(page)).toHaveCount(1, { timeout: 5_000 })
    await composerTa(page).press('Enter')

    await expect(composerTa(page)).toHaveValue('@README.md ')
    expect(await caretOf(page)).toBe('@README.md '.length)
    await expect(cmdMenu(page)).toHaveCount(0)
  })

  test('accepting a directory inserts "@dir/", keeps the menu open, and drills in', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('@src')
    await expect(cmdItems(page)).toHaveCount(4, { timeout: 5_000 })
    await cmdItems(page).nth(0).click() // the `src` directory row

    await expect(composerTa(page)).toHaveValue('@src/')
    expect(await caretOf(page)).toBe('@src/'.length)
    await expect(cmdMenu(page)).toBeVisible()
    // children only — the bare `src` row is gone from the `src/` query
    await expect(cmdItems(page)).toHaveCount(3, { timeout: 5_000 })
    await expect(cmdItems(page).nth(0)).toContainText('@src/app.js')
  })

  test('a mid-draft mention keeps the caret at the insert, not at the end', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('@ap end')
    await composerTa(page).press('ArrowLeft')
    await composerTa(page).press('ArrowLeft')
    await composerTa(page).press('ArrowLeft')
    await composerTa(page).press('ArrowLeft')
    await composerTa(page).pressSequentially('p') // caret now after "@app"
    await expect(cmdItems(page)).toHaveCount(1, { timeout: 5_000 })
    await composerTa(page).press('Enter')

    await expect(composerTa(page)).toHaveValue('@src/app.js  end')
    expect(await caretOf(page)).toBe('@src/app.js '.length)
  })

  test('two mentions in one draft resolve independently', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('@READ')
    await expect(cmdItems(page)).toHaveCount(1, { timeout: 5_000 })
    await composerTa(page).press('Enter')
    await expect(composerTa(page)).toHaveValue('@README.md ')
    await expect(cmdMenu(page)).toHaveCount(0) // the trailing space closed it

    await composerTa(page).pressSequentially('and @app')
    await expect(cmdItems(page)).toHaveCount(1, { timeout: 5_000 })
    await composerTa(page).press('Enter')

    await expect(composerTa(page)).toHaveValue('@README.md and @src/app.js ')
  })

  test('an Enter that belongs to an IME composition is not stolen as an accept', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('@READ')
    await expect(cmdItems(page)).toHaveCount(1, { timeout: 5_000 })

    // What a CJK IME sends while a candidate is still being composed.
    await composerTa(page).dispatchEvent('keydown', { key: 'Enter', isComposing: true })
    await composerTa(page).dispatchEvent('keydown', { key: 'Enter', keyCode: 229 })
    await expect(composerTa(page)).toHaveValue('@READ')
    await expect(cmdMenu(page)).toBeVisible()

    // A real Enter (composition finished) still accepts.
    await composerTa(page).press('Enter')
    await expect(composerTa(page)).toHaveValue('@README.md ')
  })

  // The list deliberately stays on screen while a newer query is in flight. It
  // must not be *acceptable* in that window: the rows answer a query the user
  // has already typed past, and the draft is one Cmd+Enter from a live agent.
  test('Enter during a superseded query refuses to insert a stale row', async ({ page }) => {
    await idleComposer(page, { fileDelayMs: 1_200 })

    await composerTa(page).fill('@src')
    await expect(cmdItems(page)).toHaveCount(4, { timeout: 8_000 })

    // `@srczzzz` matches nothing, and its own results cannot have landed yet.
    await composerTa(page).pressSequentially('zzzz')
    await composerTa(page).press('Enter')

    await expect(composerTa(page)).toHaveValue('@srczzzz')
    await expect(cmdMenu(page)).toBeVisible()
    // ...and when the current query's results do land, they say "no match" —
    // the Enter never silently completed anything.
    await expect(cmdEmpty(page)).toBeVisible({ timeout: 8_000 })
    await expect(composerTa(page)).toHaveValue('@srczzzz')
  })

  test('a slow stale response never clobbers the newer query list', async ({ page }) => {
    // `read` answers far slower than the `lib` query typed after it, so the
    // in-flight-sequence guard is the only thing keeping README.md off screen.
    await idleComposer(page, { fileDelayFor: (q) => (q === 'read' ? 1_500 : 100) })

    await composerTa(page).fill('@READ')
    await page.waitForTimeout(400) // debounce elapsed: the slow request is out
    await composerTa(page).fill('@lib')

    await expect(cmdItems(page)).toHaveCount(1, { timeout: 5_000 })
    await expect(cmdItems(page).nth(0)).toContainText('@src/lib')

    await page.waitForTimeout(1_800) // the older response lands in here
    await expect(cmdItems(page)).toHaveCount(1)
    await expect(cmdItems(page).nth(0)).toContainText('@src/lib')
  })

  test('Cmd/Ctrl+Enter still sends while a mention menu is open', async ({ page }) => {
    const { traceId } = await idleComposer(page)
    const posts = await stubBridgeSend(page, traceId, { delivered: true, detail: 'delivered to %3' })

    await composerTa(page).fill('@src')
    await expect(cmdMenu(page)).toBeVisible({ timeout: 5_000 })
    await composerTa(page).press('ControlOrMeta+Enter')

    await expect(bridgeMeta(page)).toContainText('✓ delivered to %3', { timeout: 5_000 })
    expect(posts).toEqual([{ text: '@src' }])
  })
})

// ---- the window where the rows on screen answer an older query -------------

// The list deliberately stays up while a newer query is in flight, and is
// deliberately not acceptable in that window. Both halves of making that
// window livable: it must LOOK refused, and a key pressed into it must be
// armed rather than dropped, so the ordinary flow still costs one Enter.
test.describe('stale mention window', () => {
  test('the menu shows it is not live while stale rows are on screen', async ({ page }) => {
    await idleComposer(page, { fileDelayMs: 2_000 })

    await composerTa(page).fill('@src')
    await expect(cmdItems(page)).toHaveCount(4, { timeout: 8_000 })

    // `src`'s rows are still up, but they answer a query already typed past.
    await composerTa(page).pressSequentially('zzzz')
    await expect(cmdLoading(page)).toBeVisible()
    await expect(cmdItems(page)).toHaveCount(4)
    await expect(cmdEmpty(page)).toHaveCount(0)
    const dimmed = await cmdItems(page).nth(0)
      .evaluate((el) => Number(getComputedStyle(el).opacity))
    expect(dimmed, 'rows that will refuse a click must not look live').toBeLessThan(1)

    // ...and the affordance clears once the rows answer the live query.
    await expect(cmdEmpty(page)).toBeVisible({ timeout: 8_000 })
    await expect(cmdLoading(page)).toHaveCount(0)
  })

  test('one Enter during the stale window completes the query actually typed', async ({ page }) => {
    await idleComposer(page, { fileDelayMs: 800 })

    await composerTa(page).fill('@src')
    await expect(cmdItems(page)).toHaveCount(4, { timeout: 8_000 })

    // Enter lands while `src`'s rows are on screen and `src/app`'s are out.
    await composerTa(page).pressSequentially('/app')
    await composerTa(page).press('Enter')

    // The armed accept resolves to the row for what the user typed — never the
    // `src` directory row that was highlighted at the time.
    await expect(composerTa(page)).toHaveValue('@src/app.js ', { timeout: 8_000 })
    expect(await caretOf(page)).toBe('@src/app.js '.length)
    await expect(cmdMenu(page)).toHaveCount(0)
  })

  test('typing after an armed Enter cancels it — nothing is inserted', async ({ page }) => {
    await idleComposer(page, { fileDelayMs: 800 })

    await composerTa(page).fill('@READ')
    await expect(cmdLoading(page)).toBeVisible({ timeout: 5_000 })
    await composerTa(page).press('Enter') // arms `READ`
    await composerTa(page).pressSequentially('M') // ...and supersedes it

    await expect(cmdItems(page)).toHaveCount(1, { timeout: 8_000 })
    await expect(cmdItems(page).nth(0)).toContainText('@README.md')
    await page.waitForTimeout(600) // both responses have landed by now
    await expect(composerTa(page)).toHaveValue('@READM')
  })

  test('an empty result after an armed Enter inserts nothing', async ({ page }) => {
    await idleComposer(page, { fileDelayMs: 800 })

    await composerTa(page).fill('@zzz')
    await expect(cmdLoading(page)).toBeVisible({ timeout: 5_000 })
    await composerTa(page).press('Enter')

    await expect(cmdEmpty(page)).toBeVisible({ timeout: 8_000 })
    await expect(composerTa(page)).toHaveValue('@zzz')
    await page.waitForTimeout(400)
    await expect(composerTa(page)).toHaveValue('@zzz')
  })
})

// ---- command aliases -------------------------------------------------------

// The raw terminal genuinely accepts `/reset` for `clear` and `/proactive` for
// `loop`; the endpoint ships those as an `aliases` array. The menu has to match
// them — but it completes to the canonical name, because that is the row.
test.describe('slash-command aliases (raw-terminal parity)', () => {
  test('an alias matches its command and accepts as the canonical name', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('/reset')
    await expect(cmdItems(page).first()).toContainText('/clear', { timeout: 5_000 })
    await composerTa(page).press('Enter')

    await expect(composerTa(page)).toHaveValue('/clear ')
    await expect(cmdMenu(page)).toHaveCount(0)
  })

  test('a second alias on the same row matches too', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('/proactive')
    await expect(cmdItems(page)).toHaveCount(1, { timeout: 5_000 })
    await expect(cmdItems(page).nth(0)).toContainText('/loop')
    await expect(cmdEmpty(page)).toHaveCount(0)
  })

  test('an alias-prefix match outranks a description-only substring', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('/reset')
    // `clear` by alias, `restore` only because "reset" is in its description.
    await expect(cmdItems(page)).toHaveCount(2, { timeout: 5_000 })
    await expect(cmdItems(page).nth(0)).toContainText('/clear')
    await expect(cmdItems(page).nth(1)).toContainText('/restore')
  })

  test('an alias never renders as a row of its own', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('/')
    await expect(cmdItems(page)).toHaveCount(FIXTURE_COMMANDS.length, { timeout: 5_000 })
    await expect(page.getByText('/reset', { exact: false })).toHaveCount(0)
  })

  test('a row with no aliases key at all still matches by name', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('/kindless')
    await expect(cmdItems(page)).toHaveCount(1, { timeout: 5_000 })
    await composerTa(page).press('Enter')

    await expect(composerTa(page)).toHaveValue('/kindless ')
  })
})

// ---- menu affordances shared by both modes ---------------------------------

test.describe('command menu affordances', () => {
  test('a slow file search shows the loading row, never a false "no file matches"', async ({ page }) => {
    await idleComposer(page, { fileDelayMs: 1_500 })

    await composerTa(page).fill('@zzz')
    await expect(cmdLoading(page)).toBeVisible({ timeout: 5_000 })
    await expect(cmdEmpty(page)).toHaveCount(0)
    // ...and the empty state only once the search actually came back empty.
    await expect(cmdEmpty(page)).toBeVisible({ timeout: 8_000 })
    await expect(cmdLoading(page)).toHaveCount(0)
  })

  test('a slow command catalog shows the loading row, never a false "no command matches"', async ({ page }) => {
    await idleComposer(page, { commandDelayMs: 1_500 })

    await composerTa(page).fill('/')
    await expect(cmdLoading(page)).toBeVisible({ timeout: 5_000 })
    await expect(cmdEmpty(page)).toHaveCount(0)
    await expect(cmdItems(page)).toHaveCount(FIXTURE_COMMANDS.length, { timeout: 8_000 })
    await expect(cmdLoading(page)).toHaveCount(0)
  })

  test('argumentHint renders next to the command name', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('/dep')
    await expect(cmdItems(page)).toHaveCount(1, { timeout: 5_000 })
    await expect(cmdItems(page).nth(0)).toContainText('/deploy')
    await expect(cmdItems(page).nth(0).locator('.live-cmd-hint')).toHaveText('<branch>')
  })

  test('a destructive command is badged distinctly from an ordinary kind pill', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('/')
    await expect(cmdItems(page)).toHaveCount(FIXTURE_COMMANDS.length, { timeout: 5_000 })

    const risk = page.locator('[data-testid="live-command-risk"]')
    await expect(risk).toHaveCount(1)
    await expect(risk).toHaveText('destructive')

    const riskStyle = await risk.evaluate((el) => {
      const s = getComputedStyle(el)
      return { color: s.color, bg: s.backgroundColor }
    })
    const plainStyle = await cmdItems(page).nth(0).locator('.live-cmd-kind').evaluate((el) => {
      const s = getComputedStyle(el)
      return { color: s.color, bg: s.backgroundColor }
    })
    expect(riskStyle.color, 'destructive badge must not read as a neutral kind pill')
      .not.toBe(plainStyle.color)
    expect(riskStyle.bg).not.toBe(plainStyle.bg)
  })

  test('files and directories carry their own dedicated icons', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('@')
    await expect(cmdItems(page)).toHaveCount(FIXTURE_FILES.length, { timeout: 5_000 })

    const geometry = (row) => row.locator('.live-cmd-icon path').first().getAttribute('d')
    const fileD = await geometry(cmdItems(page).nth(0)) // README.md
    const dirD = await geometry(cmdItems(page).nth(1)) // src/
    expect(fileD, 'a file row must use the real `file` glyph').toBe(FILE_GLYPH)
    expect(dirD).toBe(FOLDER_GLYPH)
    // The document-fold half of the file glyph is what tells it apart at 13px.
    await expect(cmdItems(page).nth(0).locator('.live-cmd-icon path')).toHaveCount(2)
  })

  test('a row with no kind renders no pill at all', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('/')
    await expect(cmdItems(page)).toHaveCount(FIXTURE_COMMANDS.length, { timeout: 5_000 })

    const kindless = cmdItems(page).nth(3) // the fixture row with no `kind`
    await expect(kindless).toContainText('/kindless')
    await expect(kindless.locator('.live-cmd-kind')).toHaveCount(0)
    await expect(page.locator('[class*="live-cmd-kind-undefined"]')).toHaveCount(0)
  })

  test('the composer never points ARIA at an element that does not exist', async ({ page }) => {
    await idleComposer(page, { fileDelayMs: 1_200 })

    // closed: no popup exists, so neither id may be advertised
    const shut = await ariaState(page)
    expect(shut.expanded).toBe('false')
    expect(shut.controls, 'aria-controls names a menu that is not rendered').toBeNull()
    expect(shut.active).toBeNull()

    // loading: the menu exists but renders no role=option yet
    await composerTa(page).fill('@zzz')
    await expect(cmdLoading(page)).toBeVisible({ timeout: 5_000 })
    const loading = await ariaState(page)
    expect(loading.controlsResolves).toBe(1)
    expect(loading.active, 'activedescendant points at a row the loading state has not rendered')
      .toBeNull()

    // empty: still no options
    await expect(cmdEmpty(page)).toBeVisible({ timeout: 8_000 })
    const empty = await ariaState(page)
    expect(empty.controlsResolves).toBe(1)
    expect(empty.active).toBeNull()

    // populated: the id must resolve, and to the highlighted row
    await composerTa(page).fill('@READ')
    await expect(cmdItems(page)).toHaveCount(1, { timeout: 8_000 })
    const listed = await ariaState(page)
    expect(listed.expanded).toBe('true')
    expect(listed.controlsResolves).toBe(1)
    expect(listed.activeResolves, `activedescendant ${listed.active} resolves to nothing`).toBe(1)
    expect(listed.activeIsHighlighted).toBe(true)
  })

  test('a cold command catalog is fetched once, not once per keystroke', async ({ page }) => {
    // 3s handshake: every keystroke lands while the first request is still out,
    // and the composer asks on both `input` and `keyup`.
    const { commandRequests } = await idleComposer(page, { commandDelayMs: 3_000 })

    await composerTa(page).pressSequentially('/deploying', { delay: 40 })
    await expect(cmdLoading(page)).toBeVisible({ timeout: 5_000 })
    await expect(cmdEmpty(page)).toBeVisible({ timeout: 10_000 })

    expect(commandRequests.length, `bridge-commands fetched ${commandRequests.length}×`).toBe(1)
  })

  // The catalog is cached per session for the page's lifetime — but the
  // backend's fail-closed degraded answer is a 200 with zero commands, and it
  // clears its own failure cache after 30s. Caching that answer forever would
  // pin the menu empty until a page reload.
  test('an empty command catalog is retried, not pinned empty until a reload', async ({ page }) => {
    test.setTimeout(60_000)
    const { traceId } = await idleComposer(page)
    const requests = await stubBridgeCommandsSeq(page, traceId, [[], FIXTURE_COMMANDS])

    await composerTa(page).fill('/')
    await expect(cmdEmpty(page)).toBeVisible({ timeout: 5_000 })
    expect(requests.length).toBe(1)

    await composerTa(page).fill('')
    await page.waitForTimeout(5_400) // past the empty-catalog retry window
    await composerTa(page).fill('/')

    await expect(cmdItems(page)).toHaveCount(FIXTURE_COMMANDS.length, { timeout: 8_000 })
    expect(requests.length, 'the empty catalog was never re-requested').toBe(2)
  })

  test('the fixed-position menu re-anchors to the composer on resize', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('@src')
    await expect(cmdItems(page)).toHaveCount(4, { timeout: 5_000 })

    // The menu is teleported + position:fixed off a cached rect, so a viewport
    // change that moves the composer must re-run updateMenuPos or the menu is
    // left visually detached. Width is the discriminating axis: the vertical
    // anchor is bottom-relative and survives a height change on its own.
    const offset = async () => {
      const ta = await composerTa(page).boundingBox()
      const menu = await cmdMenu(page).boundingBox()
      return { dx: menu.x - ta.x, dw: menu.width - ta.width, dy: ta.y - (menu.y + menu.height) }
    }
    const before = await offset()
    expect(Math.abs(before.dx)).toBeLessThan(2)
    expect(Math.abs(before.dw)).toBeLessThan(2)
    expect(Math.abs(before.dy), `menu sits ${before.dy}px off the composer`).toBeLessThan(12)

    await page.setViewportSize({ width: 520, height: 640 })
    await expect(cmdMenu(page)).toBeVisible()
    await expect.poll(async () => {
      const o = await offset()
      return Math.max(Math.abs(o.dx), Math.abs(o.dw), Math.abs(o.dy))
    }, { timeout: 5_000 }).toBeLessThan(12)
  })

  // Measured on the MENU's own box, not the document's: the menu is teleported
  // and `position: fixed`, so an overflowing row can never grow
  // documentElement.scrollWidth — that assertion would pass with the ellipsis
  // deleted.
  test('a very long path is clipped inside the 375px menu, not spilled out of it', async ({ page }) => {
    await idleComposer(page)

    await composerTa(page).fill('@extremely')
    await expect(cmdItems(page)).toHaveCount(1, { timeout: 5_000 })

    const geo = await cmdMenu(page).evaluate((menu) => {
      const name = menu.querySelector('.live-cmd-name')
      const m = menu.getBoundingClientRect()
      const n = name.getBoundingClientRect()
      return {
        menuScrollW: menu.scrollWidth,
        menuClientW: menu.clientWidth,
        nameRight: n.right,
        nameWidth: n.width,
        menuRight: m.right,
        docScrollW: document.documentElement.scrollWidth,
        docClientW: document.documentElement.clientWidth,
      }
    })
    expect(geo.menuScrollW, `the path scrolls the menu sideways (name is ${geo.nameWidth}px)`)
      .toBeLessThanOrEqual(geo.menuClientW + 1)
    expect(geo.nameRight, `the path runs ${geo.nameRight - geo.menuRight}px past the menu edge`)
      .toBeLessThanOrEqual(geo.menuRight + 1)
    expect(geo.docScrollW).toBeLessThanOrEqual(geo.docClientW + 1)
  })
})
