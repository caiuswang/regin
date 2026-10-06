// A pixel meter is a fixed row of cells, each wearing one part's tone, so a
// stage reads as a share without a fractional bar the font cannot draw.

export const METER_CELLS = 24

/**
 * The tone of each of `cells` cells for `parts` ({ n, tone }), in part order.
 * Shares round by largest remainder, and a non-zero part never rounds to
 * nothing: one failed task in a hundred still lights a cell. An all-zero
 * stage is every cell unstarted.
 */
export function meterCells(parts, cells = METER_CELLS) {
  const live = (parts || []).filter(p => p.n > 0)
  const total = live.reduce((n, p) => n + p.n, 0)
  if (!total) return Array(cells).fill('unstarted')
  const shares = live.map(p => {
    const exact = (p.n / total) * cells
    return { tone: p.tone, exact, k: Math.max(1, Math.floor(exact)) }
  })
  let left = cells - shares.reduce((n, s) => n + s.k, 0)
  const byRemainder = [...shares].sort((a, b) => (b.exact - b.k) - (a.exact - a.k))
  for (let i = 0; left > 0; i = (i + 1) % byRemainder.length, left--) byRemainder[i].k++
  while (left < 0) {
    const widest = shares.reduce((a, b) => (b.k > a.k ? b : a))
    widest.k--
    left++
  }
  return shares.flatMap(s => Array(s.k).fill(s.tone))
}
