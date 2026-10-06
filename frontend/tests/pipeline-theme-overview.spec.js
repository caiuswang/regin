/**
 * Feature #139, slice S3 — the pixel Overview: the loop scene and meters, the
 * projects' save slots, and the colour budget over the whole tab. The overview
 * read is stubbed so every number on screen is one this spec chose.
 */
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect } from './auth-fixture.js'

const THEMES_DIR = fileURLToPath(new URL('../src/pipeline/themes/', import.meta.url))

const part = (key, label, n) => ({ key, label, n, highlighted: false })

function loopOf({ running = 0, failed = 0, escaped = 0, triage = 0, open = 0, rest = 0, fix = 0 } = {}) {
  const stage = (label, parts) => ({ label, total: parts.reduce((s, p) => s + p.n, 0), parts })
  return {
    stages: [
      stage('Tasks', [part('idea', 'Ideas', rest), part('armed', 'Armed', rest), part('running', 'Running', running),
        part('done', 'Done', rest), part('failed', 'Failed', failed)]),
      stage('Features', [part('spec', 'Spec', rest), part('building', 'Building', rest),
        part('shipped', 'Shipped', rest), part('escaped', 'Escaped', escaped)]),
      stage('Defects', [part('triage', 'Triage', triage), part('open', 'Open', open), part('closed', 'Closed', rest)]),
    ],
    fix_tasks: fix,
  }
}

const runRow = (id, title, action) => ({
  id, title, status: 'armed', priority: 'high', kind: 'task', feature_id: 38,
  feature_title: 'Span search facets', step: { n: 3, of: 5 }, fixes_defects: [], action,
})

const scopedFeature = (id, title, stage, extra = {}) => ({
  id, title, origin: 'local', external_ref: null, stage, blurb: '', rounds: 1, sessions: 2,
  tasks: 6, tasks_done: 3, cost_usd: 4.5, gaps: {}, defects: { total: 2, open: 1, escapes: 1 },
  last_activity: '2026-10-05T06:00:00', ...extra,
})

function overview(loop, { busy = false, project = null } = {}) {
  const body = {
    scope: project ? String(project) : 'all',
    loop,
    running: busy
      ? { total: 1, rows: [{ id: 418, title: 'Stream span events', status: 'running', priority: 'high', kind: 'task',
          feature_id: 131, feature_title: 'Live trace follow', step: null, fixes_defects: [],
          live: { run_id: 1, trace_id: 'tr-418', attempt: 1, started_at: new Date().toISOString() }, turns: 14, cost_usd: 0.82 }] }
      : { total: 0, rows: [] },
    run_next: busy
      ? { total: 3, ready: 2, blocked: 1, rows: [
          runRow(421, 'Collapse repeated tool rows', { kind: 'run' }),
          runRow(426, 'Persist the stage filter', { kind: 'run' }),
          runRow(430, 'Show attribution share', { kind: 'wait', on: 418, on_status: 'running' }),
        ] }
      : { total: 0, ready: 0, rows: [] },
    needs_decision: busy
      ? { waiting: 1, all_projects: true, rows: [{ id: 97, round: 1, opened_at: '2026-10-04T10:00:00', symptom: 'Trace spine loses the live node' }] }
      : { waiting: 0, rows: [] },
    projects: busy
      ? { sort: 'attention', total: 3, active: 2, quiet: 1, rows: [
          { id: 3, scope: '3', name: 'regin-web', status: 'active', escaped: 1, last_activity: new Date().toISOString(),
            stage_bar: { spec: 2, building: 3, shipped: 4, escaped: 1 }, features: 10, open_tasks: 17, open_defects: 3 },
          { id: 4, scope: '4', name: 'eval-harness', status: 'active', escaped: 0, last_activity: null,
            stage_bar: { spec: 1, building: 1, shipped: 0, escaped: 0 }, features: 2, open_tasks: 3, open_defects: 0 },
        ] }
      : { sort: 'attention', total: 0, active: 0, quiet: 0, rows: [] },
  }
  if (project) {
    delete body.projects
    body.project_head = { id: project, name: 'regin-web', breadcrumb: ['All projects', 'regin-web'],
      stage_bar: { spec: 2, building: 3, shipped: 4, escaped: 1 }, shipped_pct: 50,
      open_tasks: 17, open_defects: 3, spent_usd: 42 }
    body.project_features = { total: 2, rows: [
      scopedFeature(131, 'Live trace follow', 'escaped'), scopedFeature(127, 'Conversation folding', 'building'),
    ] }
  }
  return body
}

async function stubOverview(page, body) {
  await page.route(/\/api\/pipeline\/overview/, route =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }))
}

async function openPixelOverview(page, appearance = 'light') {
  await page.addInitScript((t) => {
    localStorage.setItem('regin_pipeline_theme', 'pixel')
    localStorage.setItem('regin_theme', t)
  }, appearance)
  await page.goto('/overview')
  await expect.poll(() => page.evaluate(() => document.documentElement.getAttribute('data-pl-theme'))).toBe('pixel')
  await expect(page.getByTestId('loop-scene')).toBeVisible()
}

const group = (page, key) => page.getByTestId(`loop-group-${key}`)

test.describe('pixel overview', () => {
  test('test_the_loop_scene_counts_are_real', async ({ page }) => {
    await stubOverview(page, overview(loopOf({ running: 1, failed: 2, escaped: 2, triage: 4, open: 6, rest: 3 })))
    await openPixelOverview(page)

    const expected = { running: '1 RUNNING', failed: '2 FAILED', escaped: '2 ESCAPED', triage: '4 TO TRIAGE', open: '6 OPEN' }
    for (const [key, label] of Object.entries(expected)) {
      await expect(group(page, key).getByTestId('loop-group-label')).toHaveText(label, { useInnerText: true })
      await expect(group(page, key).locator('[data-actor]')).toHaveCount(Math.min(4, Number(label.split(' ')[0])))
    }
    await expect(page.getByTestId('loop-scene')).toHaveAttribute('aria-label',
      'Task board: 1 running, 2 failed. Workshop: 2 escaped. Bug den: 4 to triage, 6 open.')

    await page.unroute(/\/api\/pipeline\/overview/)
    await stubOverview(page, overview(loopOf({ running: 1, failed: 2, escaped: 2, triage: 4, open: 7, rest: 3 })))
    await page.reload()
    await expect(group(page, 'open').getByTestId('loop-group-label')).toHaveText('7 OPEN', { useInnerText: true })
    await expect(group(page, 'open').locator('[data-actor]')).toHaveCount(4)
  })

  test('test_loop_at_zero', async ({ page }) => {
    await stubOverview(page, overview(loopOf()))
    await openPixelOverview(page)

    for (const zone of ['board', 'workshop', 'den']) {
      await expect(page.getByTestId(`loop-zone-${zone}`).locator('svg[data-station]')).toBeVisible()
    }
    await expect(page.getByTestId('loop-scene').locator('[data-actor]')).toHaveCount(0)
    await expect(page.getByTestId('loop-scene').locator('[data-testid^="loop-group-"]')).toHaveCount(0)
    const tones = await page.locator('[data-pl-part="meter-cell"]').evaluateAll(els => els.map(e => e.dataset.tone))
    expect(tones).toHaveLength(72)
    expect(new Set(tones)).toEqual(new Set(['unstarted']))
    await expect(page.getByTestId('loop-total')).toHaveText(['0', '0', '0'])
    await expect(page.getByTestId('loop-note')).toHaveCount(0)
  })

  test('test_slots_honour_the_classic_contract', async ({ page }) => {
    const sources = readdirSync(THEMES_DIR, { withFileTypes: true, recursive: true })
      .filter(e => e.isFile() && /\.(js|vue)$/.test(e.name))
      .map(e => join(e.parentPath ?? e.path, e.name))
    expect(sources.length).toBeGreaterThan(3)
    const apiImports = sources.filter(f => /import[^'"]*['"][^'"]*\/api(\.js)?['"]/.test(readFileSync(f, 'utf8')))
    expect(apiImports).toEqual([])

    await page.goto('/overview')
    await expect(page.getByTestId('overview')).toBeVisible()
    const gaps = await page.evaluate(async () => {
      const contract = c => ({
        props: Object.keys(Array.isArray(c.props) ? Object.fromEntries(c.props.map(p => [p, 1])) : c.props || {}),
        emits: Array.isArray(c.emits) ? c.emits : Object.keys(c.emits || {}),
      })
      const classic = (await import('/src/pipeline/themes/classic/index.js')).default
      const pixel = (await import('/src/pipeline/themes/pixel/index.js')).default
      const out = { checked: [], missing: [] }
      for (const [name, slot] of Object.entries(pixel.slots)) {
        const base = classic.slots[name]
        if (!base) continue
        out.checked.push(name)
        const want = contract(base)
        const have = contract(slot)
        for (const p of want.props) if (!have.props.includes(p)) out.missing.push(`${name} prop ${p}`)
        for (const e of want.emits) if (!have.emits.includes(e)) out.missing.push(`${name} emit ${e}`)
      }
      return out
    })
    expect(gaps.checked).toEqual(expect.arrayContaining(['OverviewLoop', 'ProjectsPanel', 'ProjectScopePanel']))
    expect(gaps.missing).toEqual([])
  })

  // Every Overview surface: all projects (Projects slots), one project (the
  // scope panel's stage bar and task fills), and the phone's compact cards.
  const CASES = [
    { name: 'all', width: 1440, path: '/overview', ready: 'projects-panel' },
    { name: 'project', width: 1440, path: '/overview?project=3', ready: 'project-scope', project: 3 },
    { name: 'phone', width: 400, path: '/overview', ready: 'projects-panel' },
  ]
  for (const appearance of ['light', 'dark']) for (const c of CASES) {
    test(`test_colour_budget (${appearance}, ${c.name})`, async ({ page }) => {
      await page.setViewportSize({ width: c.width, height: 1000 })
      await stubOverview(page, overview(loopOf({ running: 1, failed: 2, escaped: 2, triage: 4, open: 6, rest: 3, fix: 3 }),
        { busy: true, project: c.project }))
      await page.addInitScript((t) => {
        localStorage.setItem('regin_pipeline_theme', 'pixel')
        localStorage.setItem('regin_theme', t)
      }, appearance)
      await page.goto(c.path)
      await expect.poll(() => page.evaluate(() => document.documentElement.getAttribute('data-pl-theme'))).toBe('pixel')
      await expect(page.getByTestId('running-now')).toBeVisible()
      await expect(page.getByTestId(c.ready)).toBeVisible()

      const audit = await page.evaluate(() => {
        // Slate neutrals carry a little chroma; "chromatic" is clearly more.
        const chromatic = (c) => {
          let m = c.match(/^rgba?\(([\d.]+),? ([\d.]+),? ([\d.]+)(?:,? \/? ?([\d.]+))?\)$/)
          if (m) {
            if (m[4] !== undefined && Number(m[4]) < 0.5) return false
            const [r, g, b] = [m[1], m[2], m[3]].map(Number)
            return Math.max(r, g, b) - Math.min(r, g, b) > 40
          }
          m = c.match(/^oklch\(([\d.]+) ([\d.]+) ([\d.]+|none)(?: \/ ([\d.]+))?\)$/)
          if (m) return !(m[4] !== undefined && Number(m[4]) < 0.5) && Number(m[2]) > 0.08
          return false
        }
        const COLOUR_FN = /(?:rgba?|oklch|oklab|color)\([^()]*\)/g
        const bad = []
        const primaries = new Map()
        let cells = 0
        for (const el of document.querySelectorAll('.pl-shell *')) {
          if (!el.checkVisibility() || el.closest('svg[data-sprite]')) continue
          const cs = getComputedStyle(el)
          const fills = [cs.backgroundColor, ...(cs.backgroundImage.match(COLOUR_FN) || [])].filter(chromatic)
          if (!fills.length) continue
          const hook = el.getAttribute('data-pl-part')
          if (hook === 'meter-cell') { cells++; continue }
          if (hook === 'key--primary') {
            const panel = el.closest('[data-pl-part="panel"]')
            primaries.set(panel, (primaries.get(panel) || 0) + 1)
            continue
          }
          bad.push(`${el.tagName.toLowerCase()}[${el.dataset.testid || hook || el.className}] ${fills[0]}`)
        }
        return { bad, cells, primaries: [...primaries.values()] }
      })
      expect(audit.bad).toEqual([])
      expect(audit.cells).toBeGreaterThan(0)
      expect(audit.primaries).toEqual([1])
    })
  }
})
