/**
 * The search preview marks query hits by mutating DOM that Vue owns — the
 * presentation renderers' own subtree, including MarkdownContent's v-html.
 *
 * The invariant that cannot be seen in a screenshot is that the marking is
 * reversible: Vue keeps live references to the text nodes it interpolated AND
 * uses empty text nodes as Fragment anchors, so an unwrap that re-merges too
 * greedily silently deletes anchors. That leaves a patch inserting before a
 * detached node (NotFoundError) on the next list-length change — a crash that
 * a keyed remount masks until one component is left unkeyed.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const PANEL = '[role="dialog"][aria-label="Full-text search"]'
const ROW = `${PANEL} [role="option"]`
const NEEDLE = 'zqxjrateLimit'

const emptyTextNodes = body => body.evaluate((el) => {
  const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
  let n = 0
  while (walk.nextNode()) if (walk.currentNode.nodeValue === '') n++
  return n
})

const visibleText = body =>
  body.evaluate(el => el.innerText.replace(/\s+/g, ' ').trim())

async function seed(page) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const at = i => `2026-05-08T12:00:${String(i).padStart(2, '0')}`
  const spans = [{
    trace_id: traceId, span_id: `p-${p8}`, parent_id: null, name: 'prompt',
    start_time: at(0), attributes: { text: `fix ${NEEDLE}`, is_test: true },
  }]
  let i = 1
  const add = (name, attributes) => {
    spans.push({
      trace_id: traceId, span_id: `s${i}-${p8}`, parent_id: `p-${p8}`, name,
      start_time: at(i), duration_ms: 100, attributes: { ...attributes, is_test: true },
    })
    i++
  }
  // Markdown deliberately mixes headings, inline code, a list and a fenced
  // block: each renders its own element subtree, so a match in every one of
  // them exercises marking across many separate text nodes.
  add('assistant_response', {
    text: `# ${NEEDLE} guard\n\nThe \`${NEEDLE}\` path matters.\n\n`
      + `- one ${NEEDLE}\n- two\n\n\`\`\`js\nconst ${NEEDLE} = 1\n\`\`\`\n\nTail ${NEEDLE} end.`,
  })
  add('tool.Bash', {
    tool_name: 'Bash', command: `grep ${NEEDLE} .`, stdout: `${NEEDLE} a\n${NEEDLE} b`,
  })

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
}

test('marking and unmarking leaves Vue’s DOM intact', async ({ page }) => {
  await seed(page)
  const errors = []
  page.on('pageerror', e => errors.push(String(e)))
  page.on('console', m => m.type() === 'error' && errors.push(m.text()))

  await page.keyboard.press('Meta+k')
  await page.waitForSelector(PANEL)
  const input = page.locator(`${PANEL} .query-input`)
  await input.fill(NEEDLE)
  await page.waitForTimeout(1200)
  await page.locator(ROW).filter({ hasText: 'assistant_response' }).first().click()

  const body = page.locator(`${PANEL} .preview-body`)
  await expect(body.locator('mark[data-search-hit]').first()).toBeVisible()
  // The fenced block repaints itself once its highlighter chunk lands.
  await expect(body.locator('pre code.hljs')).toBeVisible()
  await expect(body.locator('pre mark[data-search-hit]')).toBeVisible()
  const anchorsBefore = await emptyTextNodes(body)
  const textBefore = await visibleText(body)

  // Every keystroke is a clear+apply cycle; walking the query down and back up
  // runs ~22 of them over the same nodes.
  for (let n = NEEDLE.length; n >= 2; n--) await input.fill(NEEDLE.slice(0, n))
  for (let n = 2; n <= NEEDLE.length; n++) await input.fill(NEEDLE.slice(0, n))
  await page.waitForTimeout(400)

  // The anchor count is the assertion that matters: a greedy normalize() drops
  // Vue's empty text nodes and this falls.
  expect(await emptyTextNodes(body)).toBe(anchorsBefore)
  expect(await visibleText(body)).toBe(textBefore)
  await expect(body.locator('mark[data-search-hit]').first()).toBeVisible()

  // Switching rows patches a marked subtree rather than remounting a clean one.
  for (let k = 0; k < 3; k++) {
    await page.locator(ROW).filter({ hasText: 'tool.Bash' }).first().click()
    await page.locator(ROW).filter({ hasText: 'assistant_response' }).first().click()
  }
  await page.waitForTimeout(300)
  expect(await visibleText(body)).toBe(textBefore)
  expect(errors).toEqual([])
})
