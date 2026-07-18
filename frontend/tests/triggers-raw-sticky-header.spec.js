/**
 * Regression: the column headers on /trace/triggers/raw must sit at the top
 * of their own table, not float over the first body rows.
 *
 * Each table sits inside an `.overflow-x-auto` wrapper, and `overflow-x: auto`
 * forces `overflow-y` to `auto` as well — so that wrapper, not
 * `.content-scroll`, is the sticky scrollport. A `top` offset sized for the
 * page header therefore pushed every `th` that many pixels *below* its own
 * `thead`, onto the rows underneath (measured 98-128px of drift at every
 * viewport). The view now pins with `top: 0`.
 *
 * Asserted as geometry rather than CSS text so the test survives a different
 * fix: what matters is that the header renders where its thead is, and that
 * removing the drift did not reintroduce sideways scrolling.
 */
import { test, expect } from './auth-fixture.js'

const ROUTE = '/trace/triggers/raw'
const WIDTHS = [1440, 1280, 1024, 768, 390]

async function settle(page) {
  await page.waitForSelector('table.tbl thead th', { timeout: 15000 })
  await page.waitForTimeout(400)
}

test.describe('/trace/triggers/raw sticky header', () => {
  for (const width of WIDTHS) {
    test(`header does not drift onto body rows at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      await page.goto(ROUTE)
      await settle(page)

      const drifts = await page.evaluate(() => {
        const out = []
        for (const [i, t] of [...document.querySelectorAll('table.tbl')].entries()) {
          const thead = t.querySelector('thead')
          const th = t.querySelector('thead th')
          if (!thead || !th) continue
          const hb = thead.getBoundingClientRect()
          const tb = th.getBoundingClientRect()
          if (tb.width === 0) continue // table hidden at this breakpoint
          const row = t.querySelector('tbody tr')
          out.push({
            table: i,
            drift: Math.round(tb.y - hb.y),
            // negative would mean the first row is painted under the header
            headerToFirstRow: row
              ? Math.round(row.getBoundingClientRect().y - (tb.y + tb.height))
              : null,
          })
        }
        return out
      })

      expect(drifts.length).toBeGreaterThan(0)
      for (const d of drifts) {
        expect(d.drift, `table[${d.table}] th offset from its own thead`).toBe(0)
        if (d.headerToFirstRow !== null) {
          expect(
            d.headerToFirstRow,
            `table[${d.table}] first body row overlaps the header`,
          ).toBeGreaterThanOrEqual(0)
        }
      }
    })
  }

  test('page does not scroll sideways', async ({ page }) => {
    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: 900 })
      await page.goto(ROUTE)
      await settle(page)
      const over = await page.evaluate(() => {
        const s = document.querySelector('.content-scroll')
        return s ? s.scrollWidth - s.clientWidth : 0
      })
      expect(over, `horizontal overflow at ${width}px`).toBeLessThanOrEqual(1)
    }
  })
})
