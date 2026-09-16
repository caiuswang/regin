/**
 * Every colour a Badge is given must have a class behind it (CAI-252).
 *
 * `Badge.vue` builds `badge-${color}` from a free string, so a colour nobody
 * defined renders with no background at all and no error anywhere — which is
 * exactly how `escaped`, the one stage the pipeline surfaces call
 * "unmissable", shipped invisible. The rule is checked against the rendered
 * stylesheet rather than by grepping the CSS, because a class that exists but
 * resolves to an undefined token fails the same way and greps clean.
 */
import { test, expect } from './auth-fixture.js'

// Every value any STAGE_COLOR or STATE_COLOR map can produce, plus the ones
// passed as literals on the task surfaces.
const COLORS = ['green', 'yellow', 'red', 'blue', 'purple', 'gray', 'cyan',
                'amber']

test('every badge colour resolves to a real background', async ({ page }) => {
  await page.goto('/tasks')

  const unstyled = await page.evaluate((colors) => {
    const probe = document.createElement('span')
    document.body.appendChild(probe)
    const bad = []
    for (const color of colors) {
      probe.className = `badge badge-${color}`
      const style = getComputedStyle(probe)
      const bg = style.backgroundColor
      if (!bg || bg === 'transparent' || bg === 'rgba(0, 0, 0, 0)') {
        bad.push(`${color}: background ${bg}`)
      }
    }
    probe.remove()
    return bad
  }, COLORS)

  expect(unstyled).toEqual([])
})

test('escaped is visually distinct from triage', async ({ page }) => {
  await page.goto('/tasks')

  const [amber, yellow] = await page.evaluate(() => {
    const probe = document.createElement('span')
    document.body.appendChild(probe)
    const read = (cls) => {
      probe.className = `badge ${cls}`
      const s = getComputedStyle(probe)
      return `${s.backgroundColor}|${s.color}|${s.boxShadow}`
    }
    const out = [read('badge-amber'), read('badge-yellow')]
    probe.remove()
    return out
  })

  // A stage that renders identically to `triage` is not the alarm the card's
  // own comment promises.
  expect(amber).not.toBe(yellow)
})
