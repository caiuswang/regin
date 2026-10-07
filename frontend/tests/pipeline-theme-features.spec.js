/**
 * Feature #139, slice S5 — the Features tab under the pixel theme: the
 * FeatureStageTabs + StageTrack slots, the table / expansion / triage-rail
 * hooks, and the colour budget (R7) and phone mechanics (R9) they must keep.
 */
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect } from './auth-fixture.js'

const THEMES_DIR = fileURLToPath(new URL('../src/pipeline/themes/', import.meta.url))

const STAGE_COUNTS = { all: 11, spec: 3, building: 4, shipped: 3, escaped: 1 }

const feature = (id, title, stage, extra = {}) => ({
  id, title, origin: 'local', external_ref: `GH-${100 + id}`, stage, blurb: '', rounds: 2, sessions: 3,
  tasks: 4, tasks_done: 2, cost_usd: 41, gaps: {}, defects: { total: 0, open: 0, escapes: 0 },
  last_activity: '2026-10-01T06:00:00', ...extra,
})

const FEATURES = [
  feature(131, 'Live trace follow', 'building', {
    next_step: { kind: 'watch', label: 'A task is running — watch it', target: '/tasks/418' },
  }),
  feature(129, 'Cost attribution in the ledger', 'building', {
    defects: { total: 2, open: 1, escapes: 0 },
    next_step: { kind: 'resolve_defects', label: '1 open defect blocks a clean ship', target: '/features/129' },
  }),
  feature(118, 'Triage rail chords', 'escaped', { defects: { total: 4, open: 1, escapes: 1 } }),
  feature(133, 'Pipeline tab memory', 'spec', { tasks: 2, tasks_done: 0 }),
  feature(112, 'Session switcher preview', 'shipped', { tasks: 6, tasks_done: 6 }),
]

const defect = (id, extra = {}) => ({
  id, feature_id: null, symptom: `symptom ${id}`, feature_title: null, status: 'open', round: 1,
  blamed_event_kind: null, missing_check: null, suspect_paths: ['pipeline/TriageCard.vue'], fix_task_id: null,
  fix_state: null, triage_state: null, images: [], opened_at: '2026-10-04T10:00:00', ...extra,
})

const json = (route, body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })

async function stubFeatures(page) {
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url())
    const p = url.pathname
    if (route.request().method() !== 'GET') return route.fallback()
    if (p === '/api/features') {
      const stage = url.searchParams.get('stage')
      const rows = stage ? FEATURES.filter(f => f.stage === stage) : FEATURES
      return json(route, {
        items: rows, pagination: { total: rows.length, page: 0, size: 50, has_next: false, has_prev: false },
        population: STAGE_COUNTS.all, applied: { sort: url.searchParams.get('sort') || 'activity:desc' },
        extras: { stage_counts: STAGE_COUNTS, unattributed_defects: 3,
          attribution: { sessions_pct: 71, declared_usd: 1284, all_usd: 1806 } },
      })
    }
    if (p === '/api/defects') {
      if (url.searchParams.get('scope') === 'triage') {
        const items = [defect(97, { symptom: 'Trace spine loses the live node' }), defect(96, { round: 2 }), defect(94)]
        return json(route, { items, pagination: { total: items.length } })
      }
      if (url.searchParams.get('triage') === 'snoozed') return json(route, { items: [], pagination: { total: 0 } })
      if (url.searchParams.get('feature_id') === '131') {
        return json(route, { items: [defect(88, { feature_id: 131, fix_task_id: 420, fix_state: 'running' })], pagination: { total: 1 } })
      }
      return json(route, { items: [], pagination: { total: 0 } })
    }
    if (p === '/api/tasks/counts') return json(route, { by_status: { running: 1, armed: 1 } })
    if (p === '/api/tasks' && url.searchParams.get('group') === 'next_step') {
      const task = (id, title, status, extra = {}) => ({ id, title, status, waits_on: null, live: null, ...extra })
      return json(route, { groups: [
        { key: 'running', n: 1, more: 0, rows: [task(418, 'Stream span events into the trace spine', 'running',
          { live: { run_id: 7, started_at: new Date(Date.now() - 6 * 60000).toISOString() } })] },
        { key: 'blocked', n: 1, more: 0, rows: [task(419, 'Auto-follow the newest span', 'armed', { waits_on: { id: 418, status: 'running' } })] },
      ] })
    }
    if (/^\/api\/features\/\d+\/slices$/.test(p)) return json(route, { rows: [] })
    return route.fallback()
  })
}

const usePixel = page => page.addInitScript(() => localStorage.setItem('regin_pipeline_theme', 'pixel'))
const plTheme = page => page.evaluate(() => document.documentElement.getAttribute('data-pl-theme'))

async function openFeatures(page, theme) {
  await page.goto('/features')
  await expect(page.getByTestId('feature-stage-tabs')).toBeVisible()
  await expect.poll(() => plTheme(page)).toBe(theme)
  if (theme === 'pixel') await page.evaluate(() => document.fonts.ready)
}

// Runs in the page. A fill is chromatic when it is clearly saturated or is one
// of the app's tint tokens (a *-soft or a 50–300 ramp step), which measure
// close to grey but read as a colour block.
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
  // The pixel neutrals are blue-grey by design (the dark frame is #6f7f9c): a
  // role token is never a status hue, whatever its chroma.
  const neutrals = new Set(['--pl-page', '--pl-page-top', '--pl-paper', '--pl-paper-2', '--pl-paper-3', '--pl-sunk',
    '--pl-text', '--pl-text-2', '--pl-text-3', '--pl-muted', '--pl-faint', '--pl-frame', '--pl-hair', '--pl-rule',
    '--pl-bevel', '--pl-drop', '--pl-cell-off', '--pl-cell-flight', '--pl-cell-settled'].map(t => rgba(resolve(t)).join()))
  const chromatic = (color) => {
    const c = rgba(color)
    if (c[3] < 26 || neutrals.has(c.join())) return false
    return Math.max(c[0], c[1], c[2]) - Math.min(c[0], c[1], c[2]) > 40 || tints.has(c.join())
  }
  const visible = (el) => {
    const r = el.getBoundingClientRect()
    return r.width > 0 && r.height > 0 && el.checkVisibility()
  }
  const ALLOWED = '[data-pl-part="key--primary"], [data-pl-part="meter-fill"], [data-pl-part="meter-cell"]'
  const describe = el => `${el.tagName.toLowerCase()}[${el.getAttribute('data-pl-part') ?? el.getAttribute('data-testid') ?? el.className}]`
  const scopes = [...document.querySelectorAll('.pl-shell, [data-pl-part="picker"], [data-pl-part="toast"]')]
  const bad = []
  let sprites = 0
  for (const el of new Set(scopes.flatMap(s => [s, ...s.querySelectorAll('*')]))) {
    if (!visible(el)) continue
    if (el.closest('svg[data-sprite]')) { sprites++; continue }
    const allowed = el.matches(ALLOWED)
    if (chromatic(getComputedStyle(el).backgroundColor) && !allowed) {
      bad.push(`${describe(el)} fill ${getComputedStyle(el).backgroundColor}`)
    }
    for (const pseudo of ['::before', '::after']) {
      const cs = getComputedStyle(el, pseudo)
      if (cs.content !== 'none' && cs.content !== 'normal' && chromatic(cs.backgroundColor)) {
        bad.push(`${describe(el)}${pseudo} fill ${cs.backgroundColor}`)
      }
    }
  }
  const panels = [...document.querySelectorAll('[data-pl-part="panel"]')].filter(visible)
  for (const panel of panels) {
    const keys = [...panel.querySelectorAll('[data-pl-part="key--primary"]')].filter(visible)
    if (keys.length > 1) bad.push(`${describe(panel)} has ${keys.length} primary keys`)
  }
  const primaries = [...document.querySelectorAll('[data-pl-part="key--primary"]')].filter(visible).length
  return { bad, sprites, primaries, panels: panels.length }
}

test.describe('pixel theme — Features', () => {
  test('test_slots_honour_the_classic_contract', async ({ page }) => {
    const sources = readdirSync(THEMES_DIR, { withFileTypes: true, recursive: true })
      .filter(e => e.isFile() && /\.(js|vue)$/.test(e.name))
      .map(e => join(e.parentPath ?? e.path, e.name))
    const importsApi = sources.filter(f =>
      /(?:from\s+|import\s*\(\s*)['"][^'"]*\/api(?:\/index)?(?:\.js)?['"]/.test(readFileSync(f, 'utf8')))
    expect(sources.length).toBeGreaterThan(3)
    expect(importsApi).toEqual([])

    await page.goto('/overview')
    await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
    const report = await page.evaluate(async () => {
      const [pixel, classic] = await Promise.all([
        import('/src/pipeline/themes/pixel/index.js'),
        import('/src/pipeline/themes/classic/index.js'),
      ])
      const keys = v => (Array.isArray(v) ? v : Object.keys(v || {}))
      const problems = []
      const names = Object.keys(pixel.default.slots)
      for (const name of names) {
        const mine = pixel.default.slots[name]
        const theirs = classic.default.slots[name]
        if (!theirs) continue
        for (const prop of keys(theirs.props)) {
          if (!keys(mine.props).includes(prop)) problems.push(`${name} lacks prop ${prop}`)
          const req = theirs.props[prop]?.required
          if (!req && mine.props[prop]?.required) problems.push(`${name} makes ${prop} required`)
        }
        for (const ev of keys(theirs.emits)) {
          if (!keys(mine.emits).includes(ev)) problems.push(`${name} lacks emit ${ev}`)
        }
      }
      return { names, problems }
    })
    expect(report.names).toEqual(expect.arrayContaining(['FeatureStageTabs', 'StageTrack']))
    expect(report.problems).toEqual([])
  })

  test('pixel stage tabs are classic\'s control: testids, counts, select', async ({ page }) => {
    await stubFeatures(page)
    await usePixel(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await openFeatures(page, 'pixel')
    const tabs = page.getByTestId('feature-stage-tabs')
    await expect(tabs).toHaveAttribute('data-pl-part', 'stage-tabs')
    await expect(tabs.getByRole('tab')).toHaveCount(5)
    for (const [key, n] of Object.entries(STAGE_COUNTS)) {
      await expect(page.getByTestId(`feature-stage-count-${key}`)).toHaveText(String(n))
      await expect(page.getByTestId(`feature-stage-${key}`).locator('svg[data-sprite]')).toHaveCount(1)
    }
    await expect(page.getByTestId('feature-stage-all')).toHaveAttribute('aria-selected', 'true')
    await page.getByTestId('feature-stage-building').click()
    await expect(page).toHaveURL(/stage=building/)
    await expect(page.getByTestId('feature-stage-building')).toHaveAttribute('aria-selected', 'true')
  })

  test('the level track marks stage, live agent and open defects', async ({ page }) => {
    await stubFeatures(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await openFeatures(page, 'classic')
    await expect(page.getByTestId('feature-row-131').getByTestId('stage-segments')).toBeVisible()
    await expect(page.locator('[data-pl-part="level"]')).toHaveCount(0)

    await page.getByTestId('pipeline-theme-switch').click()
    await page.getByRole('option', { name: 'Pixel' }).click()
    await expect.poll(() => plTheme(page)).toBe('pixel')
    await expect(page.locator('[data-testid="stage-segments"]')).toHaveCount(0)

    const level = id => page.getByTestId(`feature-row-${id}`).locator('[data-pl-part="level"]')
    const current = id => level(id).locator('[data-pl-part="level-station"][data-current]')
    await expect(level(131)).toHaveAttribute('aria-label', 'Stage: building · agent running')
    await expect(current(131).locator('[data-pl-part="level-name"]')).toHaveText('Build')
    await expect(level(131).getByTestId('level-agent')).toBeVisible()
    await expect(level(131).getByTestId('level-bug')).toHaveCount(0)

    await expect(level(129)).toHaveAttribute('aria-label', 'Stage: building · 1 open defect')
    await expect(level(129).getByTestId('level-agent')).toHaveCount(0)
    await expect(current(129).getByTestId('level-bug')).toBeVisible()

    await expect(current(118).locator('[data-pl-part="level-name"]')).toHaveText('Ship')
    await expect(level(118).locator('svg[data-sprite="flag.escaped"]')).toBeVisible()
    await expect(current(133).locator('[data-pl-part="level-name"]')).toHaveText('Spec')
    await expect(level(133).locator('svg[data-sprite="flag.cold"]')).toBeVisible()
    await expect(level(112).locator('svg[data-sprite="flag"]')).toBeVisible()

    // Expansion: running task under the bot glyph, the waiting one under a lock.
    await page.getByTestId('feature-toggle-131').click()
    await expect(page.getByTestId('feature-active-task-418').locator('svg[data-sprite="bot.running"]')).toBeVisible()
    await expect(page.getByTestId('feature-active-task-419').locator('svg[data-sprite="lock"]')).toBeVisible()
    await expect(page.getByTestId('feature-defect-88').locator('svg[data-sprite="bug"]')).toBeVisible()
  })

  // The design's three columns: FEATURE · LEVEL · STATS, the stats stacked
  // in one cell from the same row fields and links classic spreads over four.
  test('test_features_table_has_level_and_stats_columns', async ({ page }) => {
    await stubFeatures(page)
    await usePixel(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await openFeatures(page, 'pixel')
    const head = page.getByTestId('feature-table').locator('[data-pl-part="table-head"] [role="columnheader"]')
    await expect(head).toHaveText(['Feature', 'Level · Spec → Build → Ship', 'Stats'])

    const row = id => page.getByTestId(`feature-row-${id}`)
    await expect(row(129).getByRole('cell')).toHaveCount(3)
    const stats = row(129).locator('[data-pl-part="stats"] [data-pl-part="stat-line"]')
    await expect(stats).toHaveText([/^◎\s*2\/4 tasks$/, /^↻\s*2 rounds · \d+[a-z]+/, /^✱\s*1 open of 2$/])
    await expect(stats.nth(2)).toHaveAttribute('data-warn', '')
    await expect(row(129).getByTestId('feature-tasks-link-129')).toHaveAttribute('href', /\/tasks/)
    await expect(row(129).getByTestId('feature-defects-link-129')).toHaveAttribute('href', '/features/129#feature-defects')
    await expect(row(129).getByTestId('feature-rounds')).toHaveAttribute('aria-label', '2 rounds')
    // No defect blamed: no defect line at all.
    await expect(row(133).locator('[data-pl-part="stat-line"]')).toHaveCount(2)
  })

  // The narrowest tier with the rail open: a station is ~32px, narrower than
  // a 2x bot + flag + bug, and the track must still stay inside its cell.
  for (const width of [1024, 1280, 1440]) {
    test(`the level track stays inside its cell at ${width}`, async ({ page }) => {
      await stubFeatures(page)
      await usePixel(page)
      await page.setViewportSize({ width, height: 900 })
      await openFeatures(page, 'pixel')
      await expect(page.getByTestId('feature-row-118').locator('[data-pl-part="level"]')).toBeVisible()
      const spills = await page.evaluate(() => [...document.querySelectorAll('[data-pl-part="level"]')].flatMap((level) => {
        const cell = level.closest('[role="cell"]').getBoundingClientRect()
        return [...level.querySelectorAll('svg[data-sprite], [data-pl-part="level-agent"]')]
          .map(el => el.getBoundingClientRect())
          .filter(r => r.left < cell.left - 0.5 || r.right > cell.right + 0.5)
          .map(r => `${level.closest('[data-testid^="feature-row-"]').dataset.testid}: ${r.left}–${r.right} outside ${cell.left}–${cell.right}`)
      }))
      expect(spills).toEqual([])
    })
  }

  for (const appearance of ['light', 'dark']) {
    test(`test_colour_budget ${appearance}`, async ({ page }) => {
      await stubFeatures(page)
      await page.addInitScript(t => localStorage.setItem('regin_theme', t), appearance)
      await usePixel(page)
      await page.setViewportSize({ width: 1440, height: 900 })
      await openFeatures(page, 'pixel')
      await page.getByTestId('feature-toggle-131').click()
      await expect(page.getByTestId('feature-active-task-418')).toBeVisible()
      await expect(page.getByTestId('triage-verb-accept')).toBeVisible()

      const audit = await page.evaluate(colourBudget)
      expect(audit.bad).toEqual([])
      expect(audit.sprites).toBeGreaterThan(20)
      expect(audit.primaries).toBe(1)
      expect(audit.panels).toBeGreaterThan(0)
    })
  }

  test('test_phone', async ({ page }) => {
    await stubFeatures(page)
    await usePixel(page)
    await page.setViewportSize({ width: 400, height: 860 })
    await openFeatures(page, 'pixel')

    const tabs = page.getByTestId('feature-stage-tabs')
    await expect(tabs).toHaveAttribute('data-pl-compact', '')
    const order = await tabs.getByRole('tab').evaluateAll(els => els.map(e => e.dataset.testid))
    expect(order).toEqual(['all', 'escaped', 'building', 'shipped', 'spec'].map(k => `feature-stage-${k}`))
    const heights = await tabs.getByRole('tab').evaluateAll(els => els.map(e => e.getBoundingClientRect().height))
    for (const h of heights) expect(h).toBeGreaterThanOrEqual(44)
    expect(await tabs.evaluate(el => getComputedStyle(el).overflowX)).toBe('auto')

    const create = page.getByTestId('declare-feature')
    await expect(create).toBeVisible()
    expect(await create.evaluate(el => getComputedStyle(el.closest('#pipeline-actions')).position)).toBe('fixed')

    await expect(page.getByTestId('feature-card-118').locator('[data-pl-part="level"][data-pl-compact]')).toBeVisible()
    const overflow = await page.evaluate(() => {
      const s = document.querySelector('.content-scroll')
      return s.scrollWidth - s.clientWidth
    })
    expect(overflow).toBeLessThanOrEqual(0)
  })
})
