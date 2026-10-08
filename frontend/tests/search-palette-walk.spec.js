/**
 * The palette's entity preview and its walk (feature #146, R5/R6/R8).
 *
 * `/api/palette/*` and the session lookup are answered from fixtures: what
 * these tests pin is the preview-focus state machine — which entity the pane
 * shows after ⌥↑↓ / ⌥→ / ⌥← / esc, what ↵ acts on, which actions exist — not
 * the server's relation queries, which tests/palette/test_relations.py owns.
 */
import { test, expect } from './auth-fixture.js'

const PANEL = '[role="dialog"][aria-label="Full-text search"]'
const INPUT = `${PANEL} .query-input`
const ACTIVE = `${PANEL} [role="option"][aria-selected="true"]`
const PREVIEW = `${PANEL} .entity-preview`
const CRUMBS = `${PREVIEW} nav[aria-label="Walked"] .ent-crumb`
const NOTE = `${PREVIEW} [data-testid="pivot-note"]`
const ACTIONS = `${PREVIEW} .ent-actions button`

const SID = 'c90d4471-5f2e-4a8b-9d10-7e2b3c4d5e6f'

const chip = (kind, id, ref, title) => ({ kind, id, ref, title })
const status = (word, bucket) => ({ word, bucket })

const F146 = {
  kind: 'feature', ref: 'F146', title: 'Search Palette 弹窗优化',
  status: status('building · round 2', 'active'), owners: [], context: '1/5 done · 2 open defects',
}
const T430 = {
  kind: 'task', ref: 'T430', title: 'Make “Show all” reachable by keyboard',
  status: status('needs you', 'needs_you'), owners: [chip('feature', 146, 'F146', F146.title)],
  context: 'run 1 · running',
}
const D276 = {
  kind: 'defect', ref: 'D276', title: '“+N more” under a search group cannot be reached by keyboard',
  status: status('open · accepted', 'active'), owners: [chip('feature', 146, 'F146', F146.title)],
  aside: 'fix T430', context: 'stops at the last capped row',
}
const SESSION = {
  kind: 'session', ref: '#c90d4471', title: 'Make “Show all” reachable by keyboard',
  status: status('needs you', 'needs_you'),
  owners: [chip('feature', 146, 'F146', F146.title), chip('task', 430, 'T430', T430.title)],
  context: 'asks: rm -rf build/', aside: 'approval · 6m',
}

const RELATIONS = {
  'defect/276': {
    kind: 'defect', id: 276, row: D276, lists: [],
    relations: [
      { rel: 'Belongs to', target: chip('feature', 146, 'F146', F146.title) },
      { rel: 'Reported in', target: { kind: 'session', id: SID, ref: '#c90d4471', title: SESSION.title } },
      { rel: 'Fixed by', target: chip('task', 430, 'T430', T430.title) },
    ],
    body: '“+N more” under a search group cannot be reached by keyboard',
    missing_check: 'No test walks ↓ past the capped rows.',
  },
  'task/430': {
    kind: 'task', id: 430, row: T430, body: 'Replace the +N more paragraph with a real control.',
    relations: [{ rel: 'Belongs to', target: chip('feature', 146, 'F146', F146.title) }],
    lists: [
      { label: 'Runs', total: 1, items: [{ ...SESSION, aside: 'run 1 · running' }] },
      { label: 'Defects it fixes', total: 1, items: [D276] },
    ],
  },
  [`session/${SID}`]: {
    kind: 'session', id: SID, row: SESSION, body: '',
    relations: [
      { rel: 'Belongs to', target: chip('feature', 146, 'F146', F146.title) },
      { rel: 'Runs task', target: chip('task', 430, 'T430', T430.title) },
    ],
    lists: [{ label: 'Defects reported here', total: 1, items: [D276] }],
    prompts: [{ span_id: 'span-14', ts: '2026-10-08T10:00:00', text: 'Start on D276' }],
    waiting: { kind: 'approval', text: 'rm -rf build/', destructive: true, since: '2026-10-08T10:00:00' },
  },
  'feature/146': {
    kind: 'feature', id: 146, row: F146, relations: [],
    progress: { done: 1, tasks: 5, open_defects: 2 },
    lists: [
      { label: 'Tasks', total: 1, items: [T430] },
      { label: 'Open defects', total: 1, items: [D276] },
      { label: 'Recent sessions', total: 1, items: [SESSION] },
    ],
    body: 'Make the ⌘K palette a process navigator.',
  },
}

async function mockPalette(page) {
  await page.route('**/api/palette/resolve?**', route => route.fulfill({ json: {
    ref: 'D276', address: 'defect', hits: [{ ...D276, match: 'id' }], text: [],
  } }))
  await page.route('**/api/palette/relations/**', (route) => {
    const key = decodeURIComponent(new URL(route.request().url()).pathname
      .replace(/^.*\/api\/palette\/relations\//, ''))
    const body = RELATIONS[key]
    return body ? route.fulfill({ json: body }) : route.fulfill({ status: 404, json: {} })
  })
  await page.route('**/api/session-switcher?**', (route) => {
    const url = new URL(route.request().url())
    if (!url.searchParams.get('id')) return route.continue()
    return route.fulfill({ json: { sessions: [{
      trace_id: SID, title: SESSION.title, phase: 'waiting-permission', turn: 14,
      context_pct: 62, last_seen: '2026-10-08T10:00:00',
      tail: [{ ts: '2026-10-08T10:00:00', name: 'assistant', kind: 'assistant', text: 'an option inside the listbox' }],
    }] } })
  })
}

async function openOnD276(page) {
  await mockPalette(page)
  await page.goto('/inbox')
  await page.locator('body').click()
  await page.keyboard.press('Meta+k')
  await page.waitForSelector(PANEL)
  await page.locator(INPUT).fill('D276')
  await expect(page.locator(PREVIEW)).toHaveAttribute('data-ref', 'D276')
  await expect(page.locator(`${PREVIEW} .ent-rel.is-picked`)).toHaveCount(1)
}

// D276's relations in reading order: Belongs to F146, Reported in #…, Fixed by T430.
async function walkToT430(page) {
  await page.keyboard.press('Alt+ArrowDown')
  await page.keyboard.press('Alt+ArrowDown')
  await expect(page.locator(`${PREVIEW} .ent-rel.is-picked`)).toContainText('T430')
  await page.keyboard.press('Alt+ArrowRight')
  await expect(page.locator(PREVIEW)).toHaveAttribute('data-ref', 'T430')
}

test.describe('search palette — entity preview walk', () => {
  test('test_walk_defect_to_task_to_session', async ({ page }) => {
    await openOnD276(page)
    await expect(page.locator(`${PREVIEW} .ent-rel`)).toHaveText([
      /Belongs to\s*F146/, /Reported in\s*#c90d4471/, /Fixed by\s*T430/,
    ])
    const origin = await page.locator(ACTIVE).getAttribute('id')

    await walkToT430(page)
    await expect(page.locator(CRUMBS)).toHaveText(['D276', 'T430'])
    await expect(page.locator(NOTE)).toHaveText('↵ acts on T430 · not the highlighted row')
    // The origin row stays the list's cursor, outlined dashed.
    await expect(page.locator(ACTIVE)).toHaveAttribute('id', origin)
    await expect(page.locator(ACTIVE)).toHaveCSS('outline-style', 'dashed')

    // T430: Belongs to F146, then its Runs list — the run's session.
    await page.keyboard.press('Alt+ArrowDown')
    await expect(page.locator(`${PREVIEW} .ent-item.is-picked`)).toContainText('#c90d4471')
    await page.keyboard.press('Alt+ArrowRight')
    await expect(page.locator(PREVIEW)).toHaveAttribute('data-ref', '#c90d4471')
    await expect(page.locator(CRUMBS)).toHaveText(['D276', 'T430', '#c90d4471'])
    await expect(page.locator(NOTE)).toHaveText('↵ acts on #c90d4471 · not the highlighted row')
    await expect(page.locator(`${PREVIEW} .ent-prompt`)).toContainText('Start on D276')
    await expect(page.locator(`${PREVIEW} .ent-tail`)).toContainText('an option inside the listbox')
    await expect(page.locator(ACTIVE)).toHaveAttribute('id', origin)
  })

  test('test_alt_left_restores_origin', async ({ page }) => {
    await openOnD276(page)
    const origin = await page.locator(ACTIVE).getAttribute('id')
    await walkToT430(page)

    await page.keyboard.press('Alt+ArrowLeft')
    await expect(page.locator(PREVIEW)).toHaveAttribute('data-ref', 'D276')
    await expect(page.locator(CRUMBS)).toHaveCount(0)
    await expect(page.locator(NOTE)).toHaveCount(0)
    await expect(page.locator(ACTIVE)).toHaveAttribute('id', origin)
    await expect(page.locator(ACTIVE)).not.toHaveCSS('outline-style', 'dashed')

    // esc unwinds one layer at a time: first the pivot, then the query.
    await walkToT430(page)
    await page.keyboard.press('Escape')
    await expect(page.locator(PREVIEW)).toHaveAttribute('data-ref', 'D276')
    await expect(page.locator(INPUT)).toHaveValue('D276')
    await expect(page.locator(PANEL)).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.locator(INPUT)).toHaveValue('')
    await expect(page.locator(PANEL)).toBeVisible()
  })

  test('test_enter_acts_on_pivot', async ({ page }) => {
    await openOnD276(page)
    await walkToT430(page)
    await expect(page.locator(`${PREVIEW} [data-action="primary"]`)).toContainText('Open task')
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL(/\/tasks\/430$/)
    await expect(page.locator(PANEL)).toHaveCount(0)
  })

  test('feature secondary opens its tasks filtered to the feature', async ({ page }) => {
    await openOnD276(page)
    await expect(page.locator(`${PREVIEW} .ent-rel.is-picked`)).toContainText('F146')
    await page.keyboard.press('Alt+ArrowRight')
    await expect(page.locator(PREVIEW)).toHaveAttribute('data-ref', 'F146')
    await expect(page.locator(`${PREVIEW} [data-action="secondary"]`)).toContainText('Its tasks')
    await page.keyboard.press('Meta+Enter')
    await expect(page).toHaveURL(/\/tasks\?feature_id=146$/)
  })

  test('test_waiting_card_has_no_approve', async ({ page }) => {
    await openOnD276(page)
    await page.keyboard.press('Alt+ArrowDown')
    await expect(page.locator(`${PREVIEW} .ent-rel.is-picked`)).toContainText('#c90d4471')
    await page.keyboard.press('Alt+ArrowRight')
    await expect(page.locator(PREVIEW)).toHaveAttribute('data-ref', '#c90d4471')

    const card = page.locator(`${PREVIEW} [data-testid="waiting-card"]`)
    await expect(card).toContainText('rm -rf build/')
    await expect(card).toContainText('Read-only here.')
    await expect(card).toHaveClass(/is-destructive/)
    // Red is the danger token, whatever colour space the theme writes it in.
    const [border, danger] = await card.evaluate((el) => {
      const probe = document.createElement('i')
      probe.style.color = 'var(--color-danger)'
      el.appendChild(probe)
      const out = [getComputedStyle(el).borderTopColor, getComputedStyle(probe).color]
      probe.remove()
      return out
    })
    expect(border).toBe(danger)
    await expect(card.locator('button, input, textarea')).toHaveCount(0)
    await expect(page.locator(ACTIONS)).toHaveText([/^Attach/, /^New tab/, /^Copy id/])
    await expect(page.locator(`${PREVIEW} button`, { hasText: /approve|deny|allow|reject|answer|reply/i }))
      .toHaveCount(0)
  })

  test('test_defect_actions_have_no_triage', async ({ page }) => {
    await openOnD276(page)
    await expect(page.locator(ACTIONS)).toHaveText([/^Open defect/, /^Reporter/, /^Copy id/])
    await expect(page.locator(`${PREVIEW} button`, { hasText: /accept|snooze|decline|triage/i }))
      .toHaveCount(0)
    await expect(page.locator(`${PREVIEW} section[aria-label="Missing check"]`))
      .toContainText('No test walks ↓ past the capped rows.')

    // ⌘↵ on a defect pivots to the session that reported it.
    await page.keyboard.press('Meta+Enter')
    await expect(page.locator(PREVIEW)).toHaveAttribute('data-ref', '#c90d4471')
    await expect(page.locator(CRUMBS)).toHaveText(['D276', '#c90d4471'])
  })

  test('test_palette_makes_no_write_request', async ({ page }) => {
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])
    // The app shell's SSE handshake mints a stream ticket by POST whenever
    // the event stream (re)connects; it is not the palette's, and changes no
    // state a user owns.
    const writes = []
    page.on('request', (req) => {
      const url = req.url()
      if (req.method() === 'GET' || !url.includes('/api/')) return
      if (url.includes('/api/auth/stream-ticket')) return
      writes.push(`${req.method()} ${url}`)
    })
    await openOnD276(page)
    await walkToT430(page)
    await page.keyboard.press('Alt+ArrowDown')
    await page.keyboard.press('Alt+ArrowRight')
    await expect(page.locator(PREVIEW)).toHaveAttribute('data-ref', '#c90d4471')
    await page.locator(`${PREVIEW} [data-action="copy"]`).click()
    await expect(page.locator(`${PREVIEW} [data-action="copy"]`)).toHaveText('Copied')
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(SID)
    await page.keyboard.press('Alt+ArrowLeft')
    await page.keyboard.press('Alt+ArrowLeft')
    await expect(page.locator(PREVIEW)).toHaveAttribute('data-ref', 'D276')
    await page.keyboard.press('Meta+Enter')
    await expect(page.locator(PREVIEW)).toHaveAttribute('data-ref', '#c90d4471')
    await page.keyboard.press('Escape')
    await page.keyboard.press('Escape')
    await page.keyboard.press('Escape')
    await expect(page.locator(PANEL)).toHaveCount(0)
    expect(writes).toEqual([])
  })
})
