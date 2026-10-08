/**
 * Feature #139, slice S12 — All defects under the pixel theme: R3's
 * scope widened to /defects (never /defects/:id), the DefectsHead slot, the
 * toolbar's MenuFields against the API, the card hooks and the colour budget.
 */
import { test, expect } from './auth-fixture.js'
import { pickChoice, menuValue } from './helpers/choice.js'

const defect = (id, extra = {}) => ({
  id, feature_id: null, feature_title: null, symptom: `symptom ${id}`, status: 'open', round: 1,
  blamed_event_kind: null, missing_check: null, suspect_paths: [], fix_task_id: null, fix_state: null,
  triage_state: null, triage_reason: null, queue_position: 'queue', images: [],
  opened_at: '2026-10-04T10:00:00', ...extra,
})

const DEFECTS = [
  defect(97, { symptom: 'Trace spine loses the live node after a compaction divider',
    suspect_paths: ['conversation/SpineLiveNode.vue', 'CompactionDivider.vue'] }),
  defect(96, { symptom: 'Run next shows a task that is already running in another worktree', round: 2 }),
  defect(94, { feature_id: 118, feature_title: 'Triage rail chords', symptom: 'Pressing A inside the blame picker accepts the wrong defect' }),
  defect(85, { symptom: 'Switcher preview shows the previous session', queue_position: 'snoozed', triage_state: 'snoozed' }),
  defect(81, { status: 'closed', feature_id: 129, feature_title: 'Cost attribution in the ledger', symptom: 'Cost column shows $0.00' }),
  defect(66, { status: 'closed', feature_id: 118, feature_title: 'Triage rail chords', triage_state: 'declined',
    queue_position: 'declined', symptom: 'X should ask for a reason before declining' }),
]

const json = (route, body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })

// The list requests the page made, newest last, as their query params.
async function stubDefects(page) {
  const listed = []
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url())
    const p = url.pathname
    if (route.request().method() !== 'GET') return route.fallback()
    if (p === '/api/features') {
      return json(route, {
        items: [], pagination: { total: 0, page: 0, size: 50, has_next: false, has_prev: false },
        population: 0, applied: { sort: 'activity:desc' },
        extras: { stage_counts: { all: 0 }, unattributed_defects: 2 },
      })
    }
    if (p === '/api/defects') {
      const q = url.searchParams
      if (q.get('scope') === 'triage') {
        const items = DEFECTS.slice(0, 2)
        return json(route, { items, pagination: { total: items.length } })
      }
      if (q.get('triage') === 'snoozed' && !q.get('sort')) return json(route, { items: [], pagination: { total: 0 } })
      listed.push(Object.fromEntries(q))
      const status = q.get('status')
      const triage = q.get('triage')
      const rows = DEFECTS.filter(d => (!status || d.status === status)
        && (!triage || (triage === 'queue' ? d.queue_position === 'queue' : d.triage_state === triage)))
      return json(route, {
        items: rows, pagination: { total: rows.length, page: 0, size: 50, has_next: false, has_prev: false },
        population: DEFECTS.length, applied: { sort: q.get('sort') || 'opened:desc' },
      })
    }
    return route.fallback()
  })
  return listed
}

const usePixel = page => page.addInitScript(() => localStorage.setItem('regin_pipeline_theme', 'pixel'))
const plTheme = page => page.evaluate(() => document.documentElement.getAttribute('data-pl-theme'))

async function openDefects(page) {
  await page.goto('/defects')
  await expect(page.getByTestId('defects-list')).toBeVisible()
  await expect.poll(() => plTheme(page)).toBe('pixel')
  await page.evaluate(() => document.fonts.ready)
}

// Runs in the page. A fill is chromatic when it is clearly saturated or is one
// of the app's tint tokens, which measure close to grey but read as a block.
function colourBudget() {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  const rgba = (color) => {
    ctx.clearRect(0, 0, 1, 1)
    ctx.fillStyle = 'rgba(0,0,0,0)'
    ctx.fillStyle = color
    ctx.fillRect(0, 0, 1, 1)
    return [...ctx.getImageData(0, 0, 1, 1).data]
  }
  const resolve = (token) => {
    const probe = document.createElement('span')
    probe.style.backgroundColor = `var(${token})`
    document.body.appendChild(probe)
    const c = getComputedStyle(probe).backgroundColor
    probe.remove()
    return c
  }
  const RAMPS = ['amber', 'orange', 'yellow', 'red', 'rose', 'pink', 'purple', 'violet', 'indigo', 'blue', 'sky',
    'cyan', 'teal', 'emerald', 'green', 'lime']
  const tints = new Set([
    ...['info', 'success', 'warning', 'danger', 'agent', 'orange', 'primary'].map(t => `--color-${t}-soft`),
    ...RAMPS.flatMap(r => [50, 100, 200, 300].map(n => `--color-${r}-${n}`)),
  ].map(resolve).filter(c => c !== 'rgba(0, 0, 0, 0)').map(c => rgba(c).join()))
  const chromatic = (color) => {
    const c = rgba(color)
    if (c[3] < 26) return false
    return Math.max(c[0], c[1], c[2]) - Math.min(c[0], c[1], c[2]) > 40 || tints.has(c.join())
  }
  const visible = (el) => {
    const r = el.getBoundingClientRect()
    return r.width > 0 && r.height > 0 && el.checkVisibility()
  }
  const ALLOWED = '[data-pl-part="key--primary"], [data-pl-part="meter-cell"]'
  const describe = el => `${el.tagName.toLowerCase()}[${el.getAttribute('data-pl-part') ?? el.getAttribute('data-testid') ?? el.className}]`
  const bad = []
  let sprites = 0
  const shell = document.querySelector('.pl-shell')
  for (const el of [shell, ...shell.querySelectorAll('*')]) {
    if (!visible(el)) continue
    if (el.closest('svg[data-sprite]')) { sprites++; continue }
    if (chromatic(getComputedStyle(el).backgroundColor) && !el.matches(ALLOWED)) {
      bad.push(`${describe(el)} fill ${getComputedStyle(el).backgroundColor}`)
    }
    for (const pseudo of ['::before', '::after']) {
      const cs = getComputedStyle(el, pseudo)
      if (cs.content !== 'none' && cs.content !== 'normal' && chromatic(cs.backgroundColor)) {
        bad.push(`${describe(el)}${pseudo} fill ${cs.backgroundColor}`)
      }
    }
  }
  // On this page the card is the panel R7 counts primaries in: one row, one
  // verb it waits on.
  const primaries = [...document.querySelectorAll('[data-pl-part="key--primary"]')].filter(visible)
  for (const key of primaries) {
    if (!key.closest('[data-pl-part="card"]')) bad.push(`primary key outside a card: ${key.textContent.trim()}`)
  }
  for (const card of document.querySelectorAll('[data-pl-part="card"]')) {
    const n = [...card.querySelectorAll('[data-pl-part="key--primary"]')].filter(visible).length
    if (n > 1) bad.push(`${card.dataset.testid} has ${n} primary keys`)
  }
  return { bad, sprites, primaries: primaries.map(k => k.closest('[data-pl-part="card"]').dataset.testid) }
}

test.describe('pixel theme — All defects', () => {
  test('test_all_defects_is_themed_a_defect_detail_is_not', async ({ page }) => {
    await stubDefects(page)
    await usePixel(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/features')
    await page.getByTestId('triage-unfold').click()
    await expect.poll(() => plTheme(page)).toBe('pixel')
    // The card hooks are shared with the triage rail; the defects rules stay on their page.
    const verbs = page.getByTestId('triage-card-97').locator('[data-pl-part="verbs"]')
    await expect(verbs).toBeVisible()
    expect(await verbs.evaluate(el => getComputedStyle(el).borderTopStyle)).not.toBe('dashed')
    const round = page.getByTestId('triage-card-96').locator('[data-pl-part="state"]')
    expect(await round.evaluate(el => getComputedStyle(el).fontSize)).toBe('10px')
    await page.getByTestId('triage-all-defects').click()

    await expect(page).toHaveURL(/\/defects$/)
    await expect(page.getByTestId('defects-list')).toBeVisible()
    expect(await plTheme(page)).toBe('pixel')
    const head = page.getByTestId('defects-head')
    await expect(head).toHaveText(/^FeaturesAll defects/)
    await expect(page.getByTestId('defects-head-count')).toHaveText(`${DEFECTS.length} defects`)
    await expect(page.getByTestId('pipeline-tab-features')).toHaveAttribute('aria-current', 'page')
    await expect(page.getByTestId('defects-back')).toHaveAttribute('href', '/features')

    await page.getByTestId('defect-permalink-97').click()
    await expect(page).toHaveURL(/\/defects\/97$/)
    await expect.poll(() => plTheme(page)).toBeNull()
  })

  test('classic draws no head and keeps the toolbar count', async ({ page }) => {
    await stubDefects(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/defects')
    await expect(page.getByTestId('defects-list')).toBeVisible()
    await expect.poll(() => plTheme(page)).toBe('classic')
    await expect(page.getByTestId('defects-head')).toHaveCount(0)
    await expect(page.getByTestId('list-count')).toBeVisible()
    await expect(page.locator('[data-pl-glyph]')).toHaveCount(0)
  })

  test('test_defect_filters_equal_the_api', async ({ page }) => {
    const listed = await stubDefects(page)
    await usePixel(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await openDefects(page)

    const clear = page.getByTestId('list-clear')
    await expect(clear).toBeDisabled()
    await expect(page.getByTestId('list-facet-status')).toHaveAttribute('data-pl-part', 'menu-key')
    await expect(menuValue(page, 'list-facet-status')).toHaveText('Any status')

    await pickChoice(page, 'list-facet-status', 'closed')
    await pickChoice(page, 'list-facet-triage', 'declined')
    await pickChoice(page, 'list-sort', 'round')
    await page.getByTestId('list-sort-dir').click()

    await expect(page).toHaveURL(/sort=round(%3A|:)asc/)
    await expect.poll(() => listed.at(-1)).toMatchObject({ status: 'closed', triage: 'declined', sort: 'round:asc' })
    await expect(page.getByTestId('defects-head-count')).toHaveText('1 defect')
    await expect(page.locator('[data-testid^="defect-row-"]')).toHaveCount(1)
    await expect(page.getByTestId('defect-row-66')).toBeVisible()
    await expect(page.getByTestId('list-facet-sort')).toHaveCount(0)
    await expect(page.getByTestId('list-sort')).toHaveAttribute('data-value', 'round')

    await expect(clear).toBeEnabled()
    expect(await clear.evaluate(el => getComputedStyle(el).boxShadow)).not.toBe('none')
    await clear.click()
    await expect(clear).toBeDisabled()
    expect(await clear.evaluate(el => getComputedStyle(el).boxShadow)).toBe('none')
    await expect(page.getByTestId('list-facet-status')).toHaveAttribute('data-value', '')
    await expect(page.getByTestId('list-facet-triage')).toHaveAttribute('data-value', '')
    await expect.poll(() => {
      const last = listed.at(-1)
      return [last.status || '', last.triage || '', last.sort]
    }).toEqual(['', '', 'opened:desc'])
    await expect(page.getByTestId('defects-head-count')).toHaveText(`${DEFECTS.length} defects`)
  })

  test('cards carry the design\'s marks: glyph, tags, one primary verb, ⋯ headed D-n', async ({ page }) => {
    await stubDefects(page)
    await usePixel(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await openDefects(page)

    const row = id => page.getByTestId(`defect-row-${id}`)
    await expect(row(97).locator('svg[data-sprite="bug"]')).toBeVisible()
    await expect(row(81).locator('svg[data-sprite="bug.cold"]')).toBeVisible()
    await expect(row(97)).toHaveAttribute('data-pl-unblamed', '')
    await expect(row(94)).not.toHaveAttribute('data-pl-unblamed', '')
    await expect(row(97).locator('[data-pl-part="tag"][data-tone="warn"]')).toHaveText('open')
    await expect(row(81).locator('[data-pl-part="tag"][data-tone="ship"]')).toHaveText('closed')
    await expect(row(96).locator('[data-pl-part="tag"][data-pl-dashed]')).toHaveText(/reopened/i)
    await expect(row(85).getByTestId('defect-triage-85')).toHaveAttribute('data-pl-dashed', '')

    const verb = id => page.getByTestId(`defect-primary-${id}`)
    await expect(verb(97)).toHaveAttribute('data-pl-part', 'key--primary')
    for (const id of [94, 85, 81, 66]) await expect(verb(id)).toHaveAttribute('data-pl-part', 'key')

    await page.getByTestId('defect-toggle-97').click()
    await expect(row(97)).toHaveAttribute('data-expanded', '')
    await expect(row(97).locator('[data-pl-part="facts"] dd')).toContainText('CompactionDivider.vue')
    await expect(page.getByTestId('defect-toggle-97')).toContainText('Less')

    await page.getByTestId('defect-more-97').click()
    const menu = page.locator('[data-pl-part="menu"]')
    await expect(menu).toHaveAttribute('data-pl-head', 'D-97')
    expect(await menu.evaluate(el => getComputedStyle(el, '::before').content)).toBe('"D-97"')
    await expect(menu.locator('[data-pl-part="menu-item"]').first()).toBeVisible()
    await page.keyboard.press('Escape')
  })

  for (const appearance of ['light', 'dark']) {
    test(`test_colour_budget ${appearance}`, async ({ page }) => {
      await stubDefects(page)
      await page.addInitScript(t => localStorage.setItem('regin_theme', t), appearance)
      await usePixel(page)
      await page.setViewportSize({ width: 1440, height: 900 })
      await openDefects(page)
      await page.getByTestId('defect-toggle-97').click()
      await expect(page.getByTestId('defect-row-97')).toHaveAttribute('data-expanded', '')

      const audit = await page.evaluate(colourBudget)
      expect(audit.bad).toEqual([])
      expect(audit.sprites).toBeGreaterThan(20)
      expect(audit.primaries.sort()).toEqual(['defect-row-96', 'defect-row-97'])
    })
  }

  test('phone: no sideways scroll', async ({ page }) => {
    await stubDefects(page)
    await usePixel(page)
    await page.setViewportSize({ width: 400, height: 860 })
    await openDefects(page)
    await expect(page.getByTestId('defects-head')).toBeVisible()
    const overflow = await page.evaluate(() => {
      const s = document.querySelector('.content-scroll')
      return s.scrollWidth - s.clientWidth
    })
    expect(overflow).toBeLessThanOrEqual(0)
  })
})
