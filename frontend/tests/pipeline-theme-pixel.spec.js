/**
 * Feature #139, slice S2 — the pixel theme's foundation: tokens, Silkscreen,
 * sprites, and the shell chrome (header, tabs, panels, keys, inputs, picker,
 * toasts), all reached through data-pl-part / data-pl-glyph hooks only.
 */
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import postcss from 'postcss'
import { test, expect } from './auth-fixture.js'
import { TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'

const PIXEL_DIR = fileURLToPath(new URL('../src/pipeline/themes/pixel/', import.meta.url))
const ROOT = 'html[data-pl-theme="pixel"]'

function cssFiles(dir) {
  return readdirSync(dir, { withFileTypes: true, recursive: true })
    .filter(e => e.isFile() && e.name.endsWith('.css'))
    .map(e => join(e.parentPath ?? e.path, e.name))
}

// Why a selector breaks R4, or null. Attribute values are blanked first so a
// "." or "#" inside one is not read as a class or an id.
function selectorViolation(selector) {
  const s = selector.trim()
  if (!s.startsWith(ROOT)) return 'not prefixed with html[data-pl-theme="pixel"]'
  const bare = s.replace(/\[[^\]]*\]/g, '[]').replace(/"[^"]*"/g, '""')
  if (/\.[A-Za-z_-]/.test(bare)) return 'targets a class name'
  if (bare.includes('#')) return 'targets an id'
  if (/\[\s*(class|id)\b/.test(s)) return 'targets a class name or id through an attribute'
  // A sibling combinator reaches out of the hook's subtree.
  if (/[~+](?![^[]*\])(?![^(]*\))/.test(s)) return 'uses a sibling combinator'
  if (/:deep|::v-deep|>>>|::v-global/.test(s)) return 'pierces component scope'
  const rest = s.slice(ROOT.length)
  if (/^(\[data-theme="(light|dark)"\])?$/.test(rest)) return null
  if (!/^\s+\[data-pl-(part|glyph)\b/.test(rest)) return 'does not start at a [data-pl-part] / [data-pl-glyph] hook'
  return null
}

function isRootRule(selector) {
  return /^html\[data-pl-theme="pixel"\](\[data-theme="(light|dark)"\])?$/.test(selector.trim())
}

async function seed(request) {
  const { token } = await (await request.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })).json()
  const headers = { Authorization: `Bearer ${token}` }
  const post = async (path, data) => {
    const res = await request.post(`${API_BASE}${path}`, { headers, data })
    if (!res.ok()) throw new Error(`${path}: ${res.status()} ${await res.text()}`)
    return res.json()
  }
  const stamp = Date.now()
  const feature = await post('/api/features', { title: `Pixel contrast ${stamp}` })
  await post('/api/tasks', { title: `Pixel contrast task ${stamp}`, feature_id: feature.id, priority: 'high' })
  await post('/api/defects', { symptom: `Pixel contrast defect ${stamp}` })
}

async function useAppearance(page, appearance) {
  await page.addInitScript((t) => localStorage.setItem('regin_theme', t), appearance)
}

// The suite shares one server and DB, and a mounted tab re-reads on every
// `pipeline` frame — so a row another spec writes mid-test lands between two
// snapshots. The server only sends that frame to a stream that declared the
// topic, so answering the declaration here keeps the page on the data it
// first read.
async function freezeLiveData(page) {
  await page.route('**/api/notifications/stream/topics', route => route.fulfill({ json: {} }))
}

// The header's create key mounts before the tab's own reads land; the stage
// counts, the list and the triage rail each fill in later with keys and inputs
// of their own, so a snapshot taken on the key alone measures half a tab.
async function featuresSettled(page) {
  await expect(page.getByTestId('feature-stage-count-all')).toBeVisible()
  await expect(page.locator('[data-testid="list-status"][data-state="loading"]')).toHaveCount(0)
  await expect(page.getByTestId('triage-rail').getByText('Loading…')).toHaveCount(0)
}

const plTheme = page => page.evaluate(() => document.documentElement.getAttribute('data-pl-theme'))

async function pixelReady(page) {
  await expect.poll(() => plTheme(page)).toBe('pixel')
  await page.evaluate(() => document.fonts.ready)
}

// Runs in the page. Every colour goes through a 1×1 canvas, so oklch(),
// color-mix() and hex all come back as the same sRGB bytes.
function contrastAudit() {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  const rgba = (color) => {
    ctx.clearRect(0, 0, 1, 1)
    ctx.fillStyle = 'rgba(0,0,0,0)'
    ctx.fillStyle = color
    ctx.fillRect(0, 0, 1, 1)
    const d = ctx.getImageData(0, 0, 1, 1).data
    return [d[0], d[1], d[2], d[3] / 255]
  }
  const channel = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }
  const lum = ([r, g, b]) => 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
  const ratio = (a, b) => {
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x)
    return (hi + 0.05) / (lo + 0.05)
  }
  const backdrop = (el) => {
    for (let n = el; n; n = n.parentElement) {
      const c = rgba(getComputedStyle(n).backgroundColor)
      if (c[3] > 0.5) return c
    }
    return rgba(getComputedStyle(document.documentElement).backgroundColor)
  }
  const visible = (el) => {
    const r = el.getBoundingClientRect()
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'
  }
  const describe = el => `${el.tagName.toLowerCase()}[${el.getAttribute('data-pl-part') ?? el.getAttribute('data-px') ?? ''}] "${(el.textContent || '').trim().slice(0, 30)}"`
  const scopes = [...document.querySelectorAll('.pl-shell, [data-pl-part="picker"], [data-pl-part="toast"]')]
  const within = sel => scopes.flatMap(s => [s, ...s.querySelectorAll(sel)]).filter(el => el.matches(sel))

  const failures = []
  let labels = 0
  let edges = 0
  let sprites = 0

  for (const el of within('*')) {
    const cs = getComputedStyle(el)
    if (!/^"?Silkscreen/.test(cs.fontFamily) || !visible(el)) continue
    const ownText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())
    if (!ownText || el.closest(':disabled, [aria-disabled="true"]')) continue
    labels++
    const r = ratio(rgba(cs.color), backdrop(el))
    if (r < 4.5) failures.push(`label ${describe(el)} ${r.toFixed(2)}:1 < 4.5`)
  }

  const EDGED = ['panel', 'panel-head', 'key', 'key--primary', 'chip', 'input', 'picker', 'toast', 'tab', 'head']
  for (const el of within(EDGED.map(p => `[data-pl-part="${p}"]`).join(','))) {
    if (!visible(el)) continue
    const cs = getComputedStyle(el)
    const behind = backdrop(el.parentElement ?? el)
    for (const side of ['top', 'right', 'bottom', 'left']) {
      if (parseFloat(cs[`border-${side}-width`]) < 1 || cs[`border-${side}-style`] === 'none') continue
      const color = rgba(cs[`border-${side}-color`])
      if (color[3] < 0.5) continue
      edges++
      const r = ratio(color, behind)
      if (r < 3) failures.push(`edge ${side} of ${describe(el)} ${r.toFixed(2)}:1 < 3`)
    }
  }

  for (const rect of within('svg[data-sprite] rect[data-px="k"]')) {
    sprites++
    const r = ratio(rgba(getComputedStyle(rect).fill), backdrop(rect.closest('svg').parentElement))
    if (r < 3) failures.push(`sprite ink ${describe(rect)} ${r.toFixed(2)}:1 < 3`)
  }

  return { failures, labels, edges, sprites }
}

test.describe('pixel theme', () => {
  test('test_stylesheet_stays_inside_its_hooks', () => {
    const files = cssFiles(PIXEL_DIR)
    expect(files.length).toBeGreaterThan(0)
    const problems = []
    let rules = 0
    for (const file of files) {
      const root = postcss.parse(readFileSync(file, 'utf8'), { from: file })
      root.walkAtRules('import', at => problems.push(`${file}: @import ${at.params}`))
      root.walkDecls((decl) => {
        if (decl.prop.startsWith('--') && !decl.prop.startsWith('--pl-')) {
          problems.push(`${file}: sets ${decl.prop}`)
        }
        for (const [, url] of decl.value.matchAll(/url\(\s*["']?([^"')]+)/g)) {
          if (!url.startsWith('./')) problems.push(`${file}: url(${url}) is not self-hosted`)
        }
      })
      root.walkRules((rule) => {
        rules++
        for (const selector of rule.selectors) {
          const why = selectorViolation(selector)
          if (why) problems.push(`${file}: ${selector} — ${why}`)
          // <html> itself may only receive tokens: anything else would restyle
          // the sidebar and every page outside the shell.
          if (isRootRule(selector)) {
            rule.walkDecls((d) => { if (!d.prop.startsWith('--pl-')) problems.push(`${file}: ${selector} sets ${d.prop}`) })
          }
        }
      })
    }
    expect(rules).toBeGreaterThan(0)
    expect(problems).toEqual([])
  })

  test('the hook check rejects what R4 forbids', () => {
    expect(selectorViolation('.pl-tab')).toMatch(/prefixed/)
    expect(selectorViolation(`${ROOT} .pl-tab`)).toMatch(/class/)
    expect(selectorViolation(`${ROOT} [data-pl-part="tab"] .pl-tab__count`)).toMatch(/class/)
    expect(selectorViolation(`${ROOT} body`)).toMatch(/hook/)
    expect(selectorViolation(`${ROOT} [data-pl-part="tab"] ~ aside`)).toMatch(/sibling/)
    expect(selectorViolation(`${ROOT} [data-pl-part="tab"] + *`)).toMatch(/sibling/)
    expect(selectorViolation(`${ROOT} [data-pl-part="tab"] [class*="pl-tab"]`)).toMatch(/class/)
    expect(selectorViolation(`${ROOT} [data-pl-part="key"]:not([data-pl-part~="x"])`)).toBeNull()
    expect(selectorViolation(`${ROOT} :deep([data-pl-part="tab"])`)).toMatch(/hook|scope/)
    expect(selectorViolation(`${ROOT} [data-pl-part="tab"][aria-current="page"]::before`)).toBeNull()
    expect(selectorViolation(`${ROOT}[data-theme="dark"]`)).toBeNull()
  })

  for (const appearance of ['dark', 'light']) {
    test(appearance === 'dark' ? 'test_dark_mode' : 'test_light_mode_contrast', async ({ page, request }) => {
      await seed(request)
      await freezeLiveData(page)
      await useAppearance(page, appearance)
      await page.addInitScript(() => localStorage.setItem('regin_pipeline_theme', 'pixel'))
      const external = []
      page.on('request', (r) => { if (/fonts\.(googleapis|gstatic)\.com/.test(r.url())) external.push(r.url()) })

      const totals = { labels: 0, edges: 0, sprites: 0 }
      const failures = []
      for (const path of ['/overview', '/tasks', '/features', '/defects']) {
        await page.goto(path)
        await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
        await pixelReady(page)
        expect(await page.evaluate(() => document.documentElement.getAttribute('data-theme'))).toBe(appearance)
        const tab = await page.evaluate(contrastAudit)
        failures.push(...tab.failures.map(f => `${path}: ${f}`))
        for (const k of Object.keys(totals)) totals[k] += tab[k]
      }

      await page.getByTestId('pipeline-project').click()
      await expect(page.getByTestId('project-picker')).toBeVisible()
      const picker = await page.evaluate(contrastAudit)
      failures.push(...picker.failures.map(f => `picker: ${f}`))
      for (const k of Object.keys(totals)) totals[k] += picker[k]

      expect(await page.evaluate(() => document.fonts.check('12px Silkscreen'))).toBe(true)
      expect(external).toEqual([])
      expect(totals.labels).toBeGreaterThan(10)
      expect(totals.edges).toBeGreaterThan(5)
      expect(totals.sprites).toBeGreaterThan(0)
      expect(failures).toEqual([])
    })
  }

  // I3: the shell's tabs, header and teleported layers are all pixel may touch.
  test('nothing outside the shell changes style', async ({ page }) => {
    await page.goto('/overview')
    await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
    await expect.poll(() => plTheme(page)).toBe('classic')

    const snapshot = () => page.evaluate(() => {
      const PROPS = [
        'color', 'background-color', 'background-image', 'font-family', 'font-size', 'font-weight',
        'letter-spacing', 'text-transform', 'border-top-color', 'border-bottom-color', 'border-left-color',
        'border-right-color', 'border-top-width', 'border-radius', 'box-shadow', 'outline-color', 'transform',
      ]
      const pathOf = (el) => {
        const parts = []
        for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
          parts.unshift(`${n.tagName}:${[...n.parentElement.children].indexOf(n)}`)
        }
        return parts.join('>')
      }
      const out = {}
      for (const el of [document.documentElement, ...document.body.querySelectorAll('*')]) {
        if (el.closest('.pl-shell')) continue
        const cs = getComputedStyle(el)
        out[pathOf(el)] = PROPS.map(p => cs.getPropertyValue(p)).join('|')
      }
      return out
    })

    const classic = await snapshot()
    await page.getByTestId('pipeline-theme-switch').click()
    await page.getByRole('option', { name: 'Pixel' }).click()
    await pixelReady(page)
    // The switch's own listbox closes before the snapshot.
    await expect(page.getByRole('listbox')).toHaveCount(0)
    const pixel = await snapshot()

    const changed = Object.keys(classic).filter(k => k in pixel && classic[k] !== pixel[k])
    expect(Object.keys(classic).length).toBeGreaterThan(20)
    expect(changed).toEqual([])
  })

  // R8 + R7: keys, tabs, the chip and inputs keep classic's height, and the
  // header's create key is an ink outline, not a fill.
  for (const vp of [{ name: 'desktop', width: 1440, height: 900 }, { name: 'phone', width: 400, height: 860 }]) {
    test(`hit targets are classic's at ${vp.name}`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height })
      await freezeLiveData(page)
      await page.goto('/features')
      await expect(page.getByTestId('declare-feature')).toBeVisible()
      await featuresSettled(page)
      const PARTS = ['tab', 'key', 'chip', 'input', 'theme-switch']
      const boxes = () => page.evaluate((parts) => Object.fromEntries(parts.map(p => [
        p,
        [...document.querySelectorAll(`[data-pl-part="${p}"]`)]
          .filter(el => el.getBoundingClientRect().height > 0)
          .map(el => Math.round(el.getBoundingClientRect().height)),
      ])), PARTS)

      const classic = await boxes()
      await page.getByTestId('pipeline-theme-switch').click()
      await page.getByRole('option', { name: 'Pixel' }).click()
      await pixelReady(page)
      const pixel = await boxes()

      expect(classic.tab.length).toBe(3)
      expect(classic.key.length).toBeGreaterThan(0)
      expect(pixel).toEqual(classic)

      const fill = await page.getByTestId('declare-feature').evaluate(el => getComputedStyle(el).backgroundColor)
      const [r, g, b] = fill.match(/\d+/g).map(Number)
      expect(Math.max(r, g, b) - Math.min(r, g, b)).toBeLessThan(16)
    })
  }

  test('a key press is stepped, and still under reduced motion', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('regin_pipeline_theme', 'pixel'))
    await page.goto('/features')
    await expect(page.getByTestId('declare-feature')).toBeVisible()
    await pixelReady(page)
    const key = page.getByTestId('declare-feature')
    expect(await key.evaluate(el => getComputedStyle(el).transitionTimingFunction)).toContain('steps(')

    await page.emulateMedia({ reducedMotion: 'reduce' })
    expect(await key.evaluate(el => getComputedStyle(el).transitionDuration)).toBe('0s')
  })
})
