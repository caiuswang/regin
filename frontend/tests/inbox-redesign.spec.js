import { test, expect } from './auth-fixture.js'
import {
  msg, liveDecision, staleDecision, bulletlessPermission, mockInbox,
} from './helpers/inbox-fixture.js'

async function mount(page, opts) {
  const writes = await mockInbox(page, opts)
  await page.goto('/inbox')
  await page.waitForSelector('.inbox-page')
  return writes
}

const decisionSection = (page) =>
  page.locator('.inbox-section', { hasText: 'Needs your decision' })
const headerPill = (page) => page.locator('.page-title span', { hasText: 'need' })
const needsBadges = (page) => page.locator('.inbox-row-needs')

test('0 live decisions: no section, no pill — even with stale parked cards', async ({ page }) => {
  await mount(page, { messages: [staleDecision(1), staleDecision(2), msg({ id: 3 })] })
  await expect(page.locator('[data-testid="inbox-row"]')).toHaveCount(3)
  await expect(decisionSection(page)).toHaveCount(0)
  await expect(headerPill(page)).toHaveCount(0)
  await expect(needsBadges(page)).toHaveCount(0)
})

test('1 live decision: singular copy, counts agree', async ({ page }) => {
  await mount(page, { messages: [liveDecision(1), staleDecision(2), msg({ id: 3 })] })
  await expect(decisionSection(page)).toBeVisible()
  await expect(headerPill(page)).toHaveText('1 needs a decision')
  await expect(needsBadges(page)).toHaveCount(1)
  await expect(decisionSection(page).locator('.inbox-section-count')).toHaveText('1')
})

test('N live decisions: pill == section count == badge count', async ({ page }) => {
  await mount(page, {
    messages: [liveDecision(1), liveDecision(2), liveDecision(3), staleDecision(4), msg({ id: 5 })],
  })
  await expect(headerPill(page)).toHaveText('3 need a decision')
  await expect(needsBadges(page)).toHaveCount(3)
  await expect(decisionSection(page).locator('.inbox-section-count')).toHaveText('3')
  // The remainder must land in "Latest", not vanish.
  await expect(page.locator('.inbox-section', { hasText: 'Latest' })
    .locator('.inbox-section-count')).toHaveText('2')
})

test('the decision panel lists the options and links to live', async ({ page }) => {
  await mount(page, { messages: [liveDecision(1)] })
  await page.locator('[data-testid="inbox-row"]').first().click()
  const panel = page.locator('.inbox-decision')
  await expect(panel).toBeVisible()
  await expect(panel.locator('.inbox-decision-label')).toHaveCount(2)
  await expect(panel.locator('.inbox-decision-prompt')).toContainText('Which way should this go?')
  await expect(panel.getByRole('link', { name: /Answer in live/ }))
    .toHaveAttribute('href', '/live/trace-live')
})

test('a stale parked card renders with no decision panel', async ({ page }) => {
  await mount(page, { messages: [staleDecision(1)] })
  await page.locator('[data-testid="inbox-row"]').first().click()
  await expect(page.locator('.inbox-decision')).toHaveCount(0)
})

test('selecting marks read once and clears the dot in place', async ({ page }) => {
  const writes = await mount(page, {
    messages: [msg({ id: 1, read_at: null, title: 'Unread one' }), msg({ id: 2 })],
  })
  const row = page.locator('[data-testid="inbox-row"]').first()
  await expect(row.locator('.inbox-row-dot')).toHaveCount(1)
  await row.click()
  await row.click()
  await expect(row.locator('.inbox-row-dot')).toHaveCount(0)
  const reads = writes.filter(w => w.url.endsWith('/agent-messages/read'))
  expect(reads.length, 'double-click must not double-decrement').toBe(1)
})

test('selection follows the filtered list', async ({ page }) => {
  await mount(page, {
    messages: [msg({ id: 1, title: 'Alpha result' }),
      msg({ id: 2, msg_type: 'lesson', title: 'Beta lesson' })],
  })
  await expect(page.locator('.inbox-detail-title')).toHaveText('Alpha result')
  await page.locator('[data-testid="inbox-kind-chip"]', { hasText: 'Lesson' }).click()
  await expect(page.locator('[data-testid="inbox-row"]')).toHaveCount(1)
  await expect(page.locator('.inbox-detail-title')).toHaveText('Beta lesson')
})

test('empty and no-match states render without a detail crash', async ({ page }) => {
  await mount(page, { messages: [] })
  await expect(page.locator('.inbox-placeholder')).toContainText('No messages yet')
  await expect(page.locator('.inbox-panes')).toHaveCount(0)
})

test('desktop: two panes, each scrolls, page never scrolls sideways', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await mount(page, { messages: Array.from({ length: 12 }, (_, i) => msg({ id: i + 1 })) })
  await expect(page.locator('.inbox-list')).toBeVisible()
  await expect(page.locator('.inbox-detail-pane')).toBeVisible()
  const overflow = await page.evaluate(() => {
    const el = document.querySelector('.content-scroll')
    return { x: el.scrollWidth - el.clientWidth, bodyX: document.body.scrollWidth - document.body.clientWidth }
  })
  expect(overflow.x, 'horizontal overflow in .content-scroll').toBeLessThanOrEqual(0)
  expect(overflow.bodyX, 'horizontal overflow on body').toBeLessThanOrEqual(0)
})

test('mobile: list takes over, tap opens detail, back returns', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mount(page, { messages: Array.from({ length: 6 }, (_, i) => msg({ id: i + 1 })) })
  await expect(page.locator('.inbox-list')).toBeVisible()
  await expect(page.locator('.inbox-detail-pane')).toBeHidden()

  await page.locator('[data-testid="inbox-row"]').first().click()
  await expect(page.locator('.inbox-detail-pane')).toBeVisible()
  await expect(page.locator('.inbox-list')).toBeHidden()
  // The header/toolbar yield the small viewport to the message being read.
  await expect(page.locator('.page-header')).toBeHidden()

  const x = await page.evaluate(() => {
    const el = document.querySelector('.content-scroll')
    return el.scrollWidth - el.clientWidth
  })
  expect(x, 'mobile detail must not scroll sideways').toBeLessThanOrEqual(0)

  await page.getByRole('button', { name: 'Back to message list' }).click()
  await expect(page.locator('.inbox-list')).toBeVisible()
  await expect(page.locator('.inbox-detail-pane')).toBeHidden()
  await expect(page.locator('.page-header')).toBeVisible()
})

test('a wide code block scrolls inside the body, not the pane', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const wide = '```\n' + 'x'.repeat(600) + '\n```'
  await mount(page, { messages: [msg({ id: 1, body: wide })] })
  const pane = await page.locator('.inbox-detail-pane').evaluate(
    el => el.scrollWidth - el.clientWidth)
  const pre = await page.locator('.inbox-detail-body pre').evaluate(
    el => el.scrollWidth - el.clientWidth)
  expect(pane, 'the pane itself must not overflow').toBeLessThanOrEqual(0)
  expect(pre, 'the code block should be the thing that scrolls').toBeGreaterThan(0)
})

test('a question panel replaces the body; a plan panel sits above it', async ({ page }) => {
  await mount(page, { messages: [liveDecision(1)] })
  await page.locator('[data-testid="inbox-row"]').first().click()
  await expect(page.locator('.inbox-decision')).toBeVisible()
  await expect(page.locator('.inbox-detail-body'), 'question body is duplicated')
    .toHaveCount(0)

  const plan = msg({
    id: 9, trace_id: 'trace-live', msg_type: 'warning', msg_key: 'plan-pending',
    title: 'Plan ready for review', read_at: null,
    body: '## The plan\n\n1. First step\n2. Second step',
  })
  await mount(page, { messages: [plan] })
  await page.locator('[data-testid="inbox-row"]').first().click()
  await expect(page.locator('.inbox-decision')).toBeVisible()
  await expect(page.locator('.inbox-decision-options')).toHaveCount(0)
  await expect(page.locator('.inbox-detail-body')).toContainText('First step')
})

test('mobile: the chip strip scrolls itself, not the page', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mount(page, {
    messages: [msg({ id: 1 }), msg({ id: 2, msg_type: 'lesson' }),
      msg({ id: 3, msg_type: 'warning' }), msg({ id: 4, msg_type: 'blocker' })],
  })
  const strip = await page.locator('.inbox-chips').evaluate(
    el => ({ over: el.scrollWidth - el.clientWidth, lines: el.getBoundingClientRect().height }))
  const page_x = await page.evaluate(() => {
    const el = document.querySelector('.content-scroll')
    return Math.max(el.scrollWidth - el.clientWidth,
      document.body.scrollWidth - document.body.clientWidth)
  })
  expect(strip.over, 'chips should overflow their own strip').toBeGreaterThan(0)
  expect(strip.lines, 'strip should stay one line tall').toBeLessThan(70)
  expect(page_x, 'the page itself must not scroll sideways').toBeLessThanOrEqual(0)
})

test('mobile: several rows are reachable without a nested scroller', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mount(page, { messages: Array.from({ length: 10 }, (_, i) => msg({ id: i + 1 })) })
  const nested = await page.locator('.inbox-list .inbox-scroll').evaluate(
    el => el.scrollHeight - el.clientHeight)
  expect(nested, 'the list must not be clipped into its own scroller').toBeLessThanOrEqual(1)
  await expect(page.locator('[data-testid="inbox-row"]')).toHaveCount(10)
})

test('the back control is mobile-only', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await mount(page, { messages: [msg({ id: 1 }), msg({ id: 2 })] })
  await page.locator('[data-testid="inbox-row"]').first().click()
  await expect(page.getByRole('button', { name: 'Back to message list' })).toBeHidden()

  // Reload rather than just resizing: the desktop click already opened the
  // detail, and at <lg that state hides the list the next click needs.
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await page.locator('[data-testid="inbox-row"]').first().click()
  await expect(page.getByRole('button', { name: 'Back to message list' })).toBeVisible()
})


// ── Regressions found by the independent verifier ────────────────────────

test('B1: a bullet-less permission body still renders — never swallowed', async ({ page }) => {
  await mount(page, { messages: [bulletlessPermission(1)] })
  await page.locator('[data-testid="inbox-row"]').first().click()
  await expect(page.locator('.inbox-decision')).toBeVisible()
  // No options were recoverable, so the panel must NOT claim the body.
  await expect(page.locator('.inbox-decision-options')).toHaveCount(0)
  const body = page.locator('.inbox-detail-body')
  await expect(body).toBeVisible()
  await expect(body).toContainText('needs approval to run')
  // …and it renders as markdown, not as raw `**Bash**` / `_2 option(s)_`.
  await expect(body.locator('strong')).toHaveText('Bash')
  await expect(body.locator('em')).toContainText('2 option(s)')
})

test('B1: a fenced command is never promoted into a selectable option', async ({ page }) => {
  await mount(page, {
    messages: [liveDecision(1, {
      body: 'Pick one:\n```sh\n- rm -rf /\n- ls\n```\n• Yes\n• No',
    })],
  })
  await page.locator('[data-testid="inbox-row"]').first().click()
  const labels = page.locator('.inbox-decision-label')
  await expect(labels).toHaveCount(2)
  await expect(labels).toHaveText(['Yes', 'No'])
  // It may still be shown — as a code block — but never as a choice.
  await expect(labels.filter({ hasText: 'rm -rf' })).toHaveCount(0)
  await expect(page.locator('.inbox-decision-prompt pre')).toContainText('rm -rf /')
})

test('B3: the header pill counts the whole feed, not the page', async ({ page }) => {
  // One parked agent, pushed onto page 2 by 30 ordinary messages ahead of it.
  const filler = Array.from({ length: 30 }, (_, i) => msg({ id: 200 + i, title: `Filler ${i}` }))
  await mount(page, { messages: [...filler, liveDecision(1)] })
  await expect(headerPill(page)).toHaveText('1 needs a decision')
  // It is not on this page, so the list says so instead of staying silent.
  await expect(decisionSection(page)).toHaveCount(0)
  await expect(page.locator('.inbox-offpage')).toContainText('1 more agent is waiting')
})

test('B3: a search term cannot hide a parked agent from the pill', async ({ page }) => {
  await mount(page, { messages: [liveDecision(1), msg({ id: 2, title: 'Zebra' })] })
  await expect(headerPill(page)).toHaveText('1 needs a decision')
  await page.getByRole('searchbox', { name: 'Search messages' }).fill('Zebra')
  await expect(page.locator('[data-testid="inbox-row"]')).toHaveCount(1)
  await expect(headerPill(page), 'pill must survive a search').toHaveText('1 needs a decision')
  await expect(page.locator('.inbox-offpage')).toBeVisible()
})

test('B4: unread-only — clicking a row shows THAT row, not the next one', async ({ page }) => {
  await mount(page, {
    messages: [msg({ id: 1, read_at: null, title: 'U1' }),
      msg({ id: 2, read_at: null, title: 'U2' })],
  })
  await page.locator('[data-testid="inbox-kind-chip"]').first().waitFor()
  await page.getByRole('button', { name: 'Unread only' }).click()
  const first = page.locator('[data-testid="inbox-row"]').first()
  await first.click()
  await expect(page.locator('.inbox-detail-title'), 'the clicked row must stay open')
    .toHaveText('U1')
  await expect(page.locator('[data-testid="inbox-row"]'), 'the row must not vanish')
    .toHaveCount(2)
})

test('chip counts follow the search, not just the raw feed', async ({ page }) => {
  await mount(page, {
    messages: [msg({ id: 1, msg_type: 'warning', title: 'W1 keep' }),
      msg({ id: 2, msg_type: 'warning', title: 'W2 drop' }),
      msg({ id: 3, msg_type: 'warning', title: 'W3 drop' }),
      msg({ id: 4, msg_type: 'lesson', title: 'L1 drop' })],
  })
  await page.getByRole('searchbox', { name: 'Search messages' }).fill('keep')
  await expect(page.locator('[data-testid="inbox-row"]')).toHaveCount(1)
  const warning = page.locator('[data-testid="inbox-kind-chip"]', { hasText: 'Warning' })
  await expect(warning, 'chip must count the searched set').toContainText('1')
  await expect(page.getByRole('button', { name: /^All/ })).toContainText('1')
})

test('D3: marking a parked card read keeps the answer CTA', async ({ page }) => {
  await mount(page, { messages: [liveDecision(1)] })
  await page.locator('[data-testid="inbox-row"]').first().click()
  await expect(page.getByRole('link', { name: /Answer in live/ })).toBeVisible()
  await page.getByRole('button', { name: 'Mark read' }).click()
  // It stops nagging…
  await expect(headerPill(page)).toHaveCount(0)
  await expect(needsBadges(page)).toHaveCount(0)
  // …but the agent is still parked, so the way to answer it must remain.
  await expect(page.getByRole('link', { name: /Answer in live/ })).toBeVisible()
})

test('D5: arrow keys walk the list', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await mount(page, {
    messages: [msg({ id: 1, title: 'First' }), msg({ id: 2, title: 'Second' }),
      msg({ id: 3, title: 'Third' })],
  })
  await page.locator('[data-testid="inbox-row"]').first().focus()
  await page.keyboard.press('ArrowDown')
  await expect(page.locator('.inbox-detail-title')).toHaveText('Second')
  await page.keyboard.press('ArrowDown')
  await expect(page.locator('.inbox-detail-title')).toHaveText('Third')
  await page.keyboard.press('ArrowUp')
  await expect(page.locator('.inbox-detail-title')).toHaveText('Second')
})

test('D1: the reader is never split into an unreadably narrow column', async ({ page }) => {
  // 1024 is below the split: the app sidebar leaves ~700px, so both panes
  // side by side gave the reader ~212px. It takes the viewport instead.
  await page.setViewportSize({ width: 1024, height: 800 })
  await mount(page, { messages: [msg({ id: 1 }), msg({ id: 2 })] })
  await expect(page.locator('.inbox-detail-pane')).toBeHidden()
  await page.locator('[data-testid="inbox-row"]').first().click()
  const solo = await page.locator('.inbox-detail-pane').evaluate(el => el.clientWidth)
  expect(solo, 'the reader should own the width below the split').toBeGreaterThan(600)

  // Above the split both panes are up and the reader still gets the slack.
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.reload()
  await expect(page.locator('.inbox-list')).toBeVisible()
  const split = await page.locator('.inbox-detail-pane').evaluate(el => el.clientWidth)
  expect(split, 'the list track shrinks before the reader does').toBeGreaterThan(480)
})

test('D6: the row preview carries no leaked markdown glyphs', async ({ page }) => {
  await mount(page, {
    messages: [msg({ id: 1, title: 'T',
      body: '## Head\n\n**bold** and `code`\n- one\n- two\n\n```\nfenced\n```\n[l](http://x.com/a(b))' })],
  })
  const text = await page.locator('.inbox-row-preview').first().innerText()
  expect(text).not.toMatch(/[•*`#]|\]\(|```/)
  expect(text).toContain('bold and code')
  expect(text).not.toContain('fenced')
})

test('every write the view can issue is mocked in this suite', async ({ page }) => {
  const writes = await mount(page, { messages: [msg({ id: 1, read_at: null })] })
  await page.locator('[data-testid="inbox-row"]').first().click()
  await expect(page.locator('.inbox-row-dot')).toHaveCount(0)
  expect(writes.length, 'the click should have been captured, not sent').toBeGreaterThan(0)
})

// ── Round-2 regressions (second independent review) ─────────────────────

test('R2-B1: only the "•" glyph the backend emits becomes an option', async ({ page }) => {
  await mount(page, {
    messages: [liveDecision(1, {
      // `_format_permission` puts arbitrary operator text on line 1 — a dash
      // list of shell steps is prose, not a set of answers.
      body: 'Run the release steps:\n- npm ci\n- npm run build\n- npm publish\n'
        + '_2 option(s) — approve or deny in your session._',
    })],
  })
  await page.locator('[data-testid="inbox-row"]').first().click()
  await expect(page.locator('.inbox-decision-options')).toHaveCount(0)
  const body = page.locator('.inbox-detail-body')
  await expect(body, 'the body must not be swallowed').toBeVisible()
  await expect(body).toContainText('npm publish')
})

test('R2-B1: a thematic break is not an option', async ({ page }) => {
  await mount(page, { messages: [liveDecision(1, { body: 'Approve?\n* * *\nmore text' })] })
  await page.locator('[data-testid="inbox-row"]').first().click()
  await expect(page.locator('.inbox-decision-options')).toHaveCount(0)
  await expect(page.locator('.inbox-detail-body')).toBeVisible()
})

test('R2-B1: paragraphs in a decision prompt stay separate', async ({ page }) => {
  await mount(page, {
    messages: [liveDecision(1, { body: 'First paragraph.\n\nSecond paragraph.\n• A\n• B' })],
  })
  await page.locator('[data-testid="inbox-row"]').first().click()
  await expect(page.locator('.inbox-decision-label')).toHaveCount(2)
  await expect(page.locator('.inbox-decision-prompt p')).toHaveCount(2)
})

test('R2-B3: "Show them" pages to a parked agent hidden by pagination', async ({ page }) => {
  const filler = Array.from({ length: 30 }, (_, i) => msg({ id: 200 + i, title: `Filler ${i}` }))
  await mount(page, { messages: [...filler, liveDecision(1)] })
  await expect(page.locator('.inbox-offpage')).toContainText('outside this page')
  await page.getByRole('button', { name: 'Show them' }).click()
  await expect(decisionSection(page), 'the parked card must now be on screen').toBeVisible()
  await expect(needsBadges(page)).toHaveCount(1)
  await expect(page.locator('.inbox-offpage')).toHaveCount(0)
})

test('R2-N1: the liveness probe asks for a page big enough to hold the window',
  async ({ page }) => {
    const urls = []
    page.on('request', (r) => { if (r.url().includes('/api/sessions')) urls.push(r.url()) })
    await mount(page, { messages: [msg({ id: 1 })] })
    // `limit` is not a parameter this endpoint reads — it silently served 50.
    expect(urls.join(' ')).toContain('size=')
    expect(urls.join(' ')).not.toContain('limit=')
  })

test('R2: the row preview strips tables, rules, strike and raw HTML', async ({ page }) => {
  await mount(page, {
    messages: [msg({ id: 1, title: 'T',
      body: '# H1\n> quote\n***bold-italic***\n1. numbered\n| a | b |\n| - | - |\n'
        + '~~strike~~\n<div>html</div>\n* * *\n_under_' })],
  })
  const text = await page.locator('.inbox-row-preview').first().innerText()
  expect(text, `leaked markdown in: ${text}`).not.toMatch(/[•*`#~|]|<\/?\w|\]\(/)
  expect(text).toContain('bold-italic')
  expect(text).toContain('strike')
})

test('R2: arrow keys are inert at both list boundaries', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await mount(page, { messages: [msg({ id: 1, title: 'First' }), msg({ id: 2, title: 'Last' })] })
  const rows = page.locator('[data-testid="inbox-row"]')
  await rows.first().focus()
  await page.keyboard.press('ArrowUp')
  await expect(page.locator('.inbox-detail-title')).toHaveText('First')
  await rows.last().click()
  await expect(page.locator('.inbox-detail-title')).toHaveText('Last')
  await page.keyboard.press('ArrowDown')
  await expect(page.locator('.inbox-detail-title'), 'must not wrap around').toHaveText('Last')
})
