import { test, expect } from '@playwright/test'

// Section boundaries ({ top, height } in document px) of the Claude Design
// prototype rendered at 1440 × 900 — the same render as the feature's
// screenshots 1, 3, 4, 6 and 8. Measured with getBoundingClientRect() on
// `Regin Landing.dc.html` and `Regin Docs.dc.html#/<page>` served as static
// files. The prototype's hero, Continue box, footer and PREV/NEXT carry no id
// or class there; their pins are the first <section> under <main>, the last
// <section>, the <footer> and the <nav> after the last docs section. The
// prototype is not in the repo (it is a design-tool export), so its numbers
// are pinned here; re-measure them if the design project changes.
const PROTOTYPE = {
  '/': {
    '.site-header': [0, 58],
    '#top': [82, 871],
    '#play': [1009, 718],
    '#workbench': [1783, 672],
    '#runs': [2511, 586],
    '#observability': [3153, 661],
    '#guidance': [3870, 608],
    '.continue-panel': [4534, 380],
    '.site-footer': [4986, 60],
  },
  '/pipeline': {
    '.site-header': [0, 58],
    '.world-map': [82, 528],
    '.doc-card': [82, 451],
    '#s-loop': [565, 1217],
    '#s-example': [1830, 474],
    '#s-spec': [2353, 156],
    '#s-slices': [2557, 1141],
    '#s-runs': [3745, 156],
    '#s-defects': [3949, 714],
    '#s-ship': [4712, 500],
    '#s-features': [5260, 754],
    '#s-where': [6062, 424],
    '.prev-next': [6518, 85],
  },
  '/getting-started': {
    '.site-header': [0, 58],
    '.world-map': [82, 528],
    '.doc-card': [82, 357],
    '#s-quick-start': [471, 289],
    '#s-activate-the-hooks': [789, 963],
    '#s-manual-setup': [1780, 1128],
    '#s-server-modes': [2936, 317],
    '#s-accounts-roles': [3280, 266],
    '#s-shared-vs-local-state': [3575, 501],
    '.prev-next': [4107, 85],
  },
}

const TOLERANCE = 8

// Lazy images below the fold have no height until they load, which would
// shift every boundary after them; load all of them before measuring.
async function settle(page) {
  await expect(page.locator('.hud-bar').first()).toBeVisible()
  await page.evaluate(async () => {
    const imgs = [...document.images]
    for (const img of imgs) img.loading = 'eager'
    await Promise.all(imgs.map((img) => img.decode().catch(() => {})))
    await document.fonts.ready
  })
}

test.use({ viewport: { width: 1440, height: 900 } })

for (const [path, sections] of Object.entries(PROTOTYPE)) {
  test(`${path} section boundaries match the prototype`, async ({ page }) => {
    await page.goto(path)
    await settle(page)
    const boxes = await page.evaluate((selectors) => Object.fromEntries(selectors.map((sel) => {
      const el = document.querySelector(sel)
      if (!el) return [sel, null]
      const r = el.getBoundingClientRect()
      return [sel, [Math.round(r.top + window.scrollY), Math.round(r.height)]]
    })), Object.keys(sections))

    for (const [sel, [top, height]] of Object.entries(sections)) {
      expect(boxes[sel], `${sel} is rendered`).not.toBeNull()
      const [gotTop, gotHeight] = boxes[sel]
      expect(Math.abs(gotTop - top), `${sel} top ${gotTop} vs prototype ${top}`).toBeLessThanOrEqual(TOLERANCE)
      expect(Math.abs(gotHeight - height), `${sel} height ${gotHeight} vs prototype ${height}`).toBeLessThanOrEqual(TOLERANCE)
    }
  })

  test(`${path} headings and HUD labels use the prototype's fonts`, async ({ page }) => {
    await page.goto(path)
    await settle(page)
    const fonts = await page.evaluate(() => {
      const family = (el) => getComputedStyle(el).fontFamily.split(',')[0].replace(/["']/g, '').trim()
      return {
        headings: [...document.querySelectorAll('h1, h2')].map((h) => ({
          text: h.textContent.trim().slice(0, 30), family: family(h), weight: getComputedStyle(h).fontWeight,
        })),
        huds: [...document.querySelectorAll('.hud-bar')].map((h) => ({ text: h.textContent.trim().slice(0, 30), family: family(h) })),
        // fonts.check() is true for a family with no @font-face at all, so ask
        // for a face that actually loaded.
        loaded: Object.fromEntries([['display', 'Atkinson Hyperlegible Next', 800], ['hud', 'Silkscreen', 400]]
          .map(([key, fam, weight]) => [key, [...document.fonts].some((f) => {
            const [lo, hi = lo] = f.weight.split(' ').map(Number)
            return f.family.replace(/["']/g, '') === fam && f.status === 'loaded' && weight >= lo && weight <= hi
          })])),
      }
    })

    expect(fonts.loaded).toEqual({ display: true, hud: true })
    expect(fonts.headings.length).toBeGreaterThan(0)
    for (const h of fonts.headings) {
      expect(h, `heading "${h.text}"`).toMatchObject({ family: 'Atkinson Hyperlegible Next', weight: '800' })
    }
    expect(fonts.huds.length).toBeGreaterThan(0)
    for (const h of fonts.huds) expect(h.family, `HUD "${h.text}"`).toBe('Silkscreen')
  })
}
