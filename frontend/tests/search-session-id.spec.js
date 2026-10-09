/**
 * `#<session-id>` in the search palette — the address sigil.
 *
 * `#` is not a search term: what follows it is a trace-id prefix, so the
 * palette resolves it against the session store instead of searching any
 * corpus, and ↵ attaches. The invariants worth pinning are the ones a
 * client-side filter would fail: the addressed session need not be in the
 * twelve-row roster, the digits of an id must reach the input rather than the
 * 1–9 attach hotkeys, and the sigil has to force the Sessions facet from
 * wherever the palette was opened.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const PANEL = '[role="dialog"][aria-label="Full-text search"]'
const INPUT = `${PANEL} .query-input`
const ROW = `${PANEL} [data-testid="switcher-row"]`
const EMPTY = `${PANEL} .switcher-empty`
const FACET_KEY = 'regin.search.facet'

async function seedSession(page, title) {
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const at = i => new Date(Date.now() - (6 - i) * 1000).toISOString().replace('Z', '')
  const res = await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` },
    data: [
      {
        trace_id: traceId, span_id: `p-${p8}`, parent_id: null, name: 'prompt',
        start_time: at(0), attributes: { text: title },
      },
      {
        trace_id: traceId, span_id: `s1-${p8}`, parent_id: `p-${p8}`,
        name: 'assistant_response', start_time: at(1), duration_ms: 40,
        attributes: { text: `answer for ${title}` },
      },
    ],
  })
  expect(res.ok()).toBeTruthy()
  return traceId
}

async function ready(page) {
  await expect(page.getByRole('button', { name: /quick search/i }).first())
    .toBeVisible({ timeout: 20_000 })
}

async function openPalette(page) {
  await ready(page)
  await page.keyboard.press('Meta+k')
  await page.waitForSelector(PANEL, { timeout: 15_000 })
}

test.describe('#session-id lookup', () => {
  test('resolves a pasted id and ↵ attaches to it', async ({ page }) => {
    const traceId = await seedSession(page, 'sigil fixture session')
    await page.goto('/trace/sessions')
    await openPalette(page)
    await page.locator(INPUT).fill(`#${traceId}`)

    const row = page.locator(`${ROW}[data-trace-id="${traceId}"]`)
    await expect(row).toBeVisible({ timeout: 15_000 })
    await expect(page.locator(ROW)).toHaveCount(1)
    // The sigil forces the Sessions facet even though ⌘K opened wherever the
    // reader last left the palette.
    await expect(page.locator(`${PANEL} .facet.is-active`)).toHaveText(/Sessions/)
    await expect(page.locator(`${PANEL} .query-count`)).toHaveText('1 match')

    await page.keyboard.press('Enter')
    await expect(page.locator(PANEL)).toHaveCount(0)
    await expect(page).toHaveURL(new RegExp(`/trace/sessions/${traceId}$`))
  })

  test('the digits of an id reach the input, not the 1–9 attach hotkeys',
    async ({ page }) => {
      // A uuid is mostly digits. If the roster's number chord stayed armed the
      // first numeral would navigate away mid-type.
      const traceId = await seedSession(page, 'digit fixture session')
      await page.goto('/trace/sessions')
      await openPalette(page)
      await page.locator(INPUT).fill('#')
      await page.locator(INPUT).press('1')
      await page.locator(INPUT).press('2')
      await expect(page.locator(INPUT)).toHaveValue('#12')
      await expect(page).toHaveURL(/\/trace\/sessions$/)
      // And the real id still types through end to end.
      await page.locator(INPUT).fill(`#${traceId}`)
      await expect(page.locator(`${ROW}[data-trace-id="${traceId}"]`))
        .toBeVisible({ timeout: 15_000 })
    })

  test('an id that names nothing says so instead of falling back',
    async ({ page }) => {
      await seedSession(page, 'no-fallback fixture')
      await page.goto('/trace/sessions')
      await openPalette(page)
      await page.locator(INPUT).fill('#zzzzzzzz-no-such-session')
      await expect(page.locator(EMPTY)).toHaveText(/No session id starts with/,
        { timeout: 15_000 })
      await expect(page.locator(ROW)).toHaveCount(0)
    })

  test('a bare sigil asks for more characters rather than answering',
    async ({ page }) => {
      await seedSession(page, 'floor fixture')
      await page.goto('/trace/sessions')
      await openPalette(page)
      await page.locator(INPUT).fill('#ab')
      await expect(page.locator(EMPTY)).toHaveText(/Keep typing/, { timeout: 15_000 })
    })

  test('deleting the sigil hands the palette back to search', async ({ page }) => {
    const traceId = await seedSession(page, 'handback fixture')
    await page.goto('/trace/sessions')
    await openPalette(page)
    await page.locator(INPUT).fill(`#${traceId}`)
    await expect(page.locator(ROW)).toHaveCount(1, { timeout: 15_000 })
    await page.locator(INPUT).fill('handback fixture')
    await expect(page.locator(ROW)).toHaveCount(0)
    await expect(page.locator(`${PANEL} .search-results`)).toBeVisible()
  })

  test('a re-typed prefix drops the previous answer before ↵ can attach to it',
    async ({ page }) => {
      // The rows on screen answer the PREVIOUS prefix. If they survive the
      // debounce the cursor is still pinned to session A while the input
      // reads B's id, and ↵ lands the reader in the wrong session.
      const a = await seedSession(page, 'stale row fixture A')
      const b = await seedSession(page, 'stale row fixture B')
      await page.goto('/trace/sessions')
      await openPalette(page)
      await page.locator(INPUT).fill(`#${a}`)
      await expect(page.locator(`${ROW}[data-trace-id="${a}"]`))
        .toBeVisible({ timeout: 15_000 })
      await page.locator(INPUT).fill(`#${b}`)
      await page.keyboard.press('Enter')
      await expect(page).not.toHaveURL(new RegExp(a))
    })

  test('the facet chord cannot write a preference the reader never saw move',
    async ({ page }) => {
      // Both axes are forced by the query while `#` is addressing, so tab
      // could not move the bar — persisting a pick nobody could see is how a
      // stored preference silently jumps between sessions.
      await seedSession(page, 'facet pin fixture')
      await page.goto('/trace/sessions')
      await page.evaluate(k => localStorage.setItem(k, 'Files'), FACET_KEY)
      await page.reload()
      await openPalette(page)
      await page.locator(INPUT).fill('#abcd')
      await expect(page.locator(`${PANEL} .facet.is-active`)).toHaveText(/Sessions/)
      await page.keyboard.press('Tab')
      await page.keyboard.press('Control+s')
      await expect(page.locator(`${PANEL} .facet.is-active`)).toHaveText(/Sessions/)
      expect(await page.evaluate(k => localStorage.getItem(k), FACET_KEY))
        .toBe('Files')
    })

  test('a resolved row does not advertise a 1–9 chord',
    async ({ page }) => {
      const traceId = await seedSession(page, 'hotkey chip fixture')
      await page.goto('/trace/sessions')
      await openPalette(page)
      await page.locator(INPUT).fill(`#${traceId}`)
      const row = page.locator(`${ROW}[data-trace-id="${traceId}"]`)
      await expect(row).toBeVisible({ timeout: 15_000 })
      await expect(row.locator('.switcher-hotkey')).toHaveCount(0)
    })
})
