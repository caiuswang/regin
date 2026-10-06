/**
 * Feature #139, slice S15 — the pixel dark palette, styled by role.
 *
 * `splitting the roles leaves light untouched` compares pixel light against
 * screenshots captured from the build BEFORE the role split. Both runs replay
 * the same recorded API traffic (`fixtures/pipeline-theme-pixel.har`) under the
 * clock it was recorded at, with the overview read stubbed so a LIVE row and
 * failed meter cells are on screen. Re-record with `PL_PIXEL_RECORD=1` — and
 * then recapture the baselines from a pre-S15 checkout, or the test proves
 * nothing.
 */
import { readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import postcss from 'postcss'
import { test, expect } from './auth-fixture.js'
import { TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'

const HAR = fileURLToPath(new URL('./fixtures/pipeline-theme-pixel.har', import.meta.url))
const PIXEL_DIR = fileURLToPath(new URL('../src/pipeline/themes/pixel/', import.meta.url))
const RECORD = process.env.PL_PIXEL_RECORD === '1'

const SCREENS = ['/overview', '/tasks', '/features', '/defects']
const READY = {
  '/overview': '[data-testid="needs-decision"]',
  '/tasks': 'text=Stream span events into the trace spine',
  '/features': '[data-testid="triage-verb-accept"]',
  '/defects': 'text=Ledger double-counts a resumed run',
}

async function seed(request) {
  const { token } = await (await request.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })).json()
  const headers = { Authorization: `Bearer ${token}` }
  const post = async (path, data) => {
    const res = await request.post(`${API_BASE}${path}`, { headers, data })
    if (!res.ok()) throw new Error(`${path}: ${res.status()} ${await res.text()}`)
    return res.json()
  }
  const live = await post('/api/features', { title: 'Live trace follow' })
  const ledger = await post('/api/features', { title: 'Cost attribution in the ledger' })
  await post('/api/features', { title: 'Pipeline tab memory' })
  await post('/api/tasks', { title: 'Stream span events into the trace spine', feature_id: live.id, priority: 'high' })
  await post('/api/tasks', { title: 'Auto-follow the newest span', feature_id: live.id, priority: 'urgent' })
  await post('/api/tasks', { title: 'Show attribution share in the Features subline', feature_id: ledger.id })
  await post('/api/tasks', { title: 'Add escape count to the project picker rows' })
  await post('/api/defects', { symptom: 'Trace spine loses the live node after a compaction divider' })
  await post('/api/defects', { symptom: 'Pressing A inside the blame picker accepts the wrong defect' })
  await post('/api/defects', { symptom: 'Ledger double-counts a resumed run', feature_id: ledger.id })
}

const part = (key, label, n) => ({ key, label, n, highlighted: false })
const stage = (label, parts) => ({ label, total: parts.reduce((s, p) => s + p.n, 0), parts })
const runRow = (id, title, action) => ({
  id, title, status: 'armed', priority: 'high', kind: 'task', feature_id: 38,
  feature_title: 'Span search facets', step: { n: 3, of: 5 }, fixes_defects: [], action,
})

// Fixed timestamps: the screenshot runs under the HAR's frozen clock.
const OVERVIEW = {
  scope: 'all',
  loop: {
    stages: [
      stage('Tasks', [part('idea', 'Ideas', 3), part('armed', 'Armed', 3), part('running', 'Running', 1),
        part('done', 'Done', 3), part('failed', 'Failed', 2)]),
      stage('Features', [part('spec', 'Spec', 3), part('building', 'Building', 3),
        part('shipped', 'Shipped', 3), part('escaped', 'Escaped', 2)]),
      stage('Defects', [part('triage', 'Triage', 4), part('open', 'Open', 6), part('closed', 'Closed', 3)]),
    ],
    fix_tasks: 3,
  },
  running: { total: 1, rows: [{ id: 418, title: 'Stream span events', status: 'running', priority: 'high', kind: 'task',
    feature_id: 131, feature_title: 'Live trace follow', step: null, fixes_defects: [],
    live: { run_id: 1, trace_id: 'tr-418', attempt: 1, started_at: '2026-10-01T09:00:00Z' }, turns: 14, cost_usd: 0.82 }] },
  run_next: { total: 3, ready: 2, blocked: 1, rows: [
    runRow(421, 'Collapse repeated tool rows', { kind: 'run' }),
    runRow(426, 'Persist the stage filter', { kind: 'run' }),
    runRow(430, 'Show attribution share', { kind: 'wait', on: 418, on_status: 'running' }),
  ] },
  needs_decision: { waiting: 1, all_projects: true,
    rows: [{ id: 97, round: 1, opened_at: '2026-10-01T08:00:00', symptom: 'Trace spine loses the live node' }] },
  projects: { sort: 'attention', total: 2, active: 2, quiet: 0, rows: [
    { id: 3, scope: '3', name: 'regin-web', status: 'active', escaped: 1, last_activity: '2026-10-01T08:00:00Z',
      stage_bar: { spec: 2, building: 3, shipped: 4, escaped: 1 }, features: 10, open_tasks: 17, open_defects: 3 },
    { id: 4, scope: '4', name: 'eval-harness', status: 'active', escaped: 0, last_activity: null,
      stage_bar: { spec: 1, building: 1, shipped: 0, escaped: 0 }, features: 2, open_tasks: 3, open_defects: 0 },
  ] },
}

function recordedAt() {
  return Date.parse(JSON.parse(readFileSync(HAR, 'utf8')).log.entries[0].startedDateTime)
}

function harForThisPort(testInfo, baseURL) {
  const path = testInfo.outputPath('pixel.har')
  writeFileSync(path, readFileSync(HAR, 'utf8').replace(/http:\/\/localhost:\d+/g, new URL(baseURL).origin))
  return path
}

async function replay(page, testInfo, baseURL, appearance) {
  await page.routeFromHAR(harForThisPort(testInfo, baseURL), { url: '**/api/**', notFound: 'abort' })
  // Registered after the HAR, so it wins for the overview read.
  await page.route(/\/api\/pipeline\/overview/, route =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(OVERVIEW) }))
  await page.clock.setFixedTime(recordedAt())
  await page.addInitScript((t) => {
    localStorage.setItem('regin_pipeline_theme', 'pixel')
    localStorage.setItem('regin_theme', t)
  }, appearance)
  await page.setViewportSize({ width: 1440, height: 900 })
}

async function open(page, path) {
  await page.goto(path)
  await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.documentElement.getAttribute('data-pl-theme'))).toBe('pixel')
  await expect(page.locator(READY[path]).first()).toBeVisible()
  await expect(page.getByTestId('pipeline-count-tasks')).toBeVisible()
  await page.evaluate(() => document.fonts.ready)
}

test('record the pixel fixture', async ({ page, request }) => {
  test.skip(!RECORD, 'set PL_PIXEL_RECORD=1 to re-record')
  await seed(request)
  await page.addInitScript(() => localStorage.setItem('regin_pipeline_theme', 'pixel'))
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.routeFromHAR(HAR, { url: '**/api/**', update: true, updateContent: 'embed' })
  for (const path of SCREENS) {
    await page.goto(path)
    await expect(page.locator(READY[path]).first()).toBeVisible()
    await expect(page.getByTestId('pipeline-count-tasks')).toBeVisible()
  }
})

test.describe('pixel dark palette', () => {
  test.skip(RECORD, 'recording')

  test('test_splitting_the_roles_leaves_light_untouched', async ({ page, baseURL }, testInfo) => {
    await replay(page, testInfo, baseURL, 'light')
    for (const path of SCREENS) {
      await open(page, path)
      await expect(page).toHaveScreenshot(`pixel-light${path.replace('/', '-')}.png`, {
        fullPage: true,
        animations: 'disabled',
      })
    }
  })

  test('test_dark_palette_matches_the_design_by_role', async ({ page, baseURL }, testInfo) => {
    await replay(page, testInfo, baseURL, 'dark')
    const hex = (selector, prop, pseudo) => page.locator(selector).first().evaluate((el, [p, ps]) => {
      const c = document.createElement('canvas')
      c.width = c.height = 1
      const ctx = c.getContext('2d')
      ctx.fillStyle = getComputedStyle(el, ps || null)[p]
      ctx.fillRect(0, 0, 1, 1)
      return '#' + [...ctx.getImageData(0, 0, 1, 1).data.slice(0, 3)].map(v => v.toString(16).padStart(2, '0')).join('')
    }, [prop, pseudo])

    for (const path of SCREENS) {
      await open(page, path)
      expect(await page.evaluate(() => document.documentElement.getAttribute('data-theme'))).toBe('dark')
      expect(await hex('[data-pl-part="shell"]', 'backgroundColor'), path).toBe('#0b0e16')
      const panel = path === '/defects' ? '[data-pl-part="sheet"]' : 'section[data-pl-part="panel"]'
      expect(await hex(panel, 'backgroundColor'), path).toBe('#151b27')
      expect(await hex(panel, 'borderTopColor'), path).toBe('#6f7f9c')
      expect(await hex('[data-pl-part="tab"][aria-current="page"]', 'color'), path).toBe('#e6ecf5')
      expect(await hex('[data-pl-part="meta-label"]', 'color'), path).toBe('#97a4ba')
    }

    await open(page, '/overview')
    expect(await hex('[data-pl-part="panel-title"]', 'color')).toBe('#e6ecf5')
    expect(await hex('[data-pl-part="panel-meta"]', 'color')).toBe('#97a4ba')
    expect(await hex('[data-pl-part="panel-link"]', 'color')).toBe('#7aa9ff')
    expect(await hex('[data-pl-part="key--primary"]', 'backgroundColor')).toBe('#2563eb')
    expect(await hex('[data-pl-part="key--primary"]', 'color')).toBe('#ffffff')
    // The LIVE square is a mark drawn as a 2px inset edge; its hue is the solid fill's.
    expect(await page.locator('[data-pl-part="live"] i').first().evaluate(el => getComputedStyle(el).boxShadow))
      .toContain('rgb(124, 58, 237)')
    expect(await hex('[data-pl-part="meter-cell"][data-tone="danger"]', 'backgroundColor')).toBe('#dc2626')
    expect(await hex('[data-px="k"]', 'fill')).toBe('#d3dbe8')

    // The design's dark file draws these near-white with pale labels; R15 does not copy it.
    await open(page, '/features')
    const ratio = await page.getByTestId('triage-verb-dup').evaluate((el) => {
      const c = document.createElement('canvas')
      c.width = c.height = 1
      const ctx = c.getContext('2d')
      const rgb = (color) => { ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = color; ctx.fillRect(0, 0, 1, 1); return [...ctx.getImageData(0, 0, 1, 1).data.slice(0, 3)] }
      const ch = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }
      const lum = ([r, g, b]) => 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)
      const cs = getComputedStyle(el)
      const [hi, lo] = [lum(rgb(cs.color)), lum(rgb(cs.backgroundColor))].sort((a, b) => b - a)
      return (hi + 0.05) / (lo + 0.05)
    })
    expect(ratio).toBeGreaterThanOrEqual(4.5)
    for (const verb of ['dup', 'decline', 'snooze']) {
      expect(await hex(`[data-testid="triage-verb-${verb}"]`, 'backgroundColor'), verb).toBe('#151b27')
      expect(await hex(`[data-testid="triage-verb-${verb}"]`, 'color'), verb).toBe('#cdd6e4')
    }
  })
})

function sourceFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name)
    if (e.isDirectory()) return sourceFiles(p)
    return /\.(css|js|vue)$/.test(e.name) ? [p] : []
  })
}

const COLOUR = /#[0-9a-f]{3,8}\b|\brgba?\(|\bhsla?\(|\boklch\(|var\(--color-/gi

// The palette blocks are tokens.css's two root rules: the only place a colour
// literal may live. Everything else names a role.
function strayColours(file, text) {
  if (file.endsWith('.css')) {
    const found = []
    postcss.parse(text, { from: file }).walkDecls((decl) => {
      const root = decl.parent.type === 'rule' && /^html\[data-pl-theme="pixel"\](\[data-theme="dark"\])?$/.test(decl.parent.selector.trim())
      if (root && file.endsWith('tokens.css') && decl.prop.startsWith('--pl-')) {
        if (/var\(--color-/.test(decl.value)) found.push(`${decl.prop}: ${decl.value}`)
        return
      }
      for (const m of decl.value.matchAll(COLOUR)) found.push(`${decl.prop}: ${m[0]}`)
    })
    return found
  }
  // A .vue's <style> is scanned like a stylesheet with no palette block; its
  // script and template may not carry a colour either.
  return [...text.matchAll(COLOUR)].map(m => m[0])
}

test('test_no_hard_coded_colours_in_the_pixel_theme', () => {
  const files = sourceFiles(PIXEL_DIR)
  expect(files.some(f => f.endsWith('tokens.css'))).toBe(true)
  const problems = files.flatMap(f => strayColours(f, readFileSync(f, 'utf8')).map(s => `${f.slice(PIXEL_DIR.length)}: ${s}`))
  expect(problems).toEqual([])
})

test('the colour scan catches what R15 forbids', () => {
  expect(strayColours('x/menus.css', 'html[data-pl-theme="pixel"] [data-pl-part="a"] { color: #fff; }')).toHaveLength(1)
  expect(strayColours('x/menus.css', 'html[data-pl-theme="pixel"] [data-pl-part="a"] { box-shadow: 0 0 0 1px rgba(0,0,0,.1); }')).toHaveLength(1)
  expect(strayColours('x/tokens.css', 'html[data-pl-theme="pixel"] { --pl-text: #0f172a; }')).toHaveLength(0)
  expect(strayColours('x/tokens.css', 'html[data-pl-theme="pixel"] { --pl-paper: var(--color-surface); }')).toHaveLength(1)
  expect(strayColours('x/tokens.css', 'html[data-pl-theme="pixel"] [data-pl-part="a"] { color: #0f172a; }')).toHaveLength(1)
  expect(strayColours('x/OverviewLoop.vue', '<style>.a { color: var(--color-fg); }</style>')).toHaveLength(1)
  expect(strayColours('x/sprites.js', "const KEYS = ['k', 'w']")).toHaveLength(0)
})
