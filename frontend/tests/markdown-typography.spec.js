/**
 * `.pattern-content` is the ONE stylesheet behind every markdown surface in
 * regin — trace messages, patterns, inbox, memory, the Pipeline's spec and task
 * bodies. It styled only part of the language, so a spec written in the house
 * notation lost exactly the constructs that notation is made of.
 *
 * Every assertion here reads COMPUTED STYLE, never a class name: the invariant
 * is "an h4 looks like a heading", which a class named `.h4` does not prove.
 */
import { test, expect } from './auth-fixture.js'

// One probe body carrying every construct the fix names, so a single page load
// measures all six.
const BODY = [
  '#### A fourth-level heading',
  '',
  '##### A fifth-level heading',
  '',
  '###### A sixth-level heading',
  '',
  'A paragraph with a [link](https://example.com) and `inline code` in it.',
  '',
  '> A quoted line.',
  '',
  '- [ ] an unchecked task',
  '- [x] a checked task',
  '',
  '---',
  '',
  'A closing paragraph.',
].join('\n')

function task(overrides = {}) {
  return {
    id: 80, title: 'a specced feature', body: BODY, status: 'idea',
    source: 'web', tags: [], links: [], params: {},
    autorun: false, project_id: null, priority: 'none', priority_rank: 4,
    parent_id: null, assignee: null, assignee_name: null, rounds: 1,
    created_at: '2026-09-14', updated_at: '2026-09-14',
    external_id: null, created_by: null, ...overrides,
  }
}

async function openProbe(page) {
  await page.route(/\/api\/tasks\/80$/, (route) =>
    route.fulfill({ json: { task: task(), grain: { children: 0, runs: 0 } } }))
  await page.route(/\/api\/tasks\/80\/comments$/, (route) =>
    route.fulfill({ json: { threads: [] } }))
  await page.route(/\/api\/tasks\/80\/links$/, (route) =>
    route.fulfill({ json: { links: [] } }))
  await page.goto('/tasks/80')
  const body = page.locator('.tbe-body')
  await expect(body.locator('h4')).toBeVisible()
  return body
}

const styleOf = (locator, props) => locator.evaluate((el, names) => {
  const cs = getComputedStyle(el)
  return Object.fromEntries(names.map((n) => [n, cs[n]]))
}, props)

const px = (v) => parseFloat(v) || 0

async function setTheme(page, theme) {
  await page.evaluate((t) => document.documentElement
    .setAttribute('data-theme', t), theme)
}

test('a fourth-level heading is a heading', async ({ page }) => {
  const body = await openProbe(page)
  const p = await styleOf(body.locator('p').first(), ['fontSize', 'fontWeight'])

  // h5/h6 ride along: they shared h4's defect (13px / weight 400, byte-identical
  // to the paragraph) and would regress the same way.
  for (const tag of ['h4', 'h5', 'h6']) {
    const h = await styleOf(body.locator(tag), ['fontSize', 'fontWeight'])
    const heavier = parseInt(h.fontWeight, 10) > parseInt(p.fontWeight, 10)
    const larger = px(h.fontSize) > px(p.fontSize)
    expect(heavier || larger,
      `${tag} ${h.fontWeight}/${h.fontSize} vs p ${p.fontWeight}/${p.fontSize}`)
      .toBe(true)
  }
})

test('a link looks like a link', async ({ page }) => {
  const body = await openProbe(page)
  const prose = await styleOf(body.locator('p').first(), ['color'])
  const link = await styleOf(body.locator('a').first(),
    ['color', 'textDecorationLine'])

  expect(link.color).not.toBe(prose.color)
  expect(link.textDecorationLine).toContain('underline')
})

test('a link stays distinguishable under data-theme="dark"',
  async ({ page }) => {
    const body = await openProbe(page)
    await setTheme(page, 'dark')

    const prose = await styleOf(body.locator('p').first(), ['color'])
    const link = await styleOf(body.locator('a').first(),
      ['color', 'textDecorationLine'])

    expect(link.color).not.toBe(prose.color)
    expect(link.textDecorationLine).toContain('underline')
  })

test('a blockquote is set apart', async ({ page }) => {
  const body = await openProbe(page)
  const prose = await styleOf(body.locator('p').first(), ['color'])
  const quote = await styleOf(body.locator('blockquote'),
    ['color', 'borderLeftWidth', 'borderLeftStyle', 'paddingLeft', 'marginLeft'])

  const ruled = px(quote.borderLeftWidth) > 0 && quote.borderLeftStyle !== 'none'
  const inset = px(quote.paddingLeft) > 0 || px(quote.marginLeft) > 0
  const tinted = quote.color !== prose.color

  expect((ruled && inset) || tinted,
    `blockquote ${JSON.stringify(quote)} vs prose ${prose.color}`).toBe(true)
})

test('a task list shows checkboxes, not checkboxes-and-bullets',
  async ({ page }) => {
    const body = await openProbe(page)
    const items = body.locator('li').filter({ has: page.locator('input[type=checkbox]') })
    await expect(items).toHaveCount(2)

    for (const item of await items.all()) {
      const { listStyleType } = await styleOf(item, ['listStyleType'])
      expect(listStyleType).toBe('none')
    }

    // The plain bullets elsewhere must be untouched — this widens the
    // stylesheet, it does not strip markers from every list.
    const plain = body.locator('ul > li').filter({
      hasNot: page.locator('input[type=checkbox]'),
    })
    if (await plain.count()) {
      const { listStyleType } = await styleOf(plain.first(), ['listStyleType'])
      expect(listStyleType).not.toBe('none')
    }
  })

test('a rule separates', async ({ page }) => {
  const body = await openProbe(page)
  const hr = await styleOf(body.locator('hr'), ['marginTop', 'marginBottom'])

  expect(px(hr.marginTop)).toBeGreaterThan(0)
  expect(px(hr.marginBottom)).toBeGreaterThan(0)
})

test('inline code matches its sentence', async ({ page }) => {
  const body = await openProbe(page)
  const sentence = body.locator('p').filter({ hasText: 'A paragraph with' })
  const p = await styleOf(sentence, ['fontSize'])
  const code = await styleOf(sentence.locator('code'), ['fontSize'])

  expect(px(code.fontSize)).toBeCloseTo(px(p.fontSize), 1)
})
