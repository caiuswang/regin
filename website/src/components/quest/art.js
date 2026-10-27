// Canvas pixel art for the quest map. Every colour is read from the site's
// Palette custom properties at mount, so the map cannot drift off-palette.
export const P = {}
const TOKENS = {
  bg: 'bg', panel: 'panel', raised: 'panel-raised', rule: 'rule', edge: 'edge', ink: 'ink',
  body: 'body', soft: 'soft', muted: 'muted', dim: 'dim', green: 'green', greenHi: 'green-hover',
  greenDeep: 'green-deep', greenWash: 'green-wash', greenInk: 'green-ink', gold: 'gold',
}
const SHADOW = 'rgba(0, 0, 0, 0.55)'

export function loadPalette(el) {
  const cs = getComputedStyle(el)
  for (const [key, name] of Object.entries(TOKENS)) P[key] = cs.getPropertyValue(`--${name}`).trim()
  P.byToken = Object.fromEntries(Object.entries(TOKENS).map(([key, name]) => [name, P[key]]))
}

const ROOFS = {
  green: ['green', 'greenDeep'], deep: ['greenDeep', 'greenWash'],
  soft: ['soft', 'muted'], gold: ['gold', 'dim'],
}

export function rect(ctx, color, x, y, w, h) {
  ctx.fillStyle = color
  ctx.fillRect(Math.round(x), Math.round(y), w, h)
}

function boxed(ctx, fill, x, y, w, h, outline = P.bg) {
  rect(ctx, outline, x - 1, y - 1, w + 2, h + 2)
  rect(ctx, fill, x, y, w, h)
}

function shadow(ctx, cx, by, w) {
  rect(ctx, SHADOW, cx - w / 2, by - 1, w, 3)
}

// rows: one char per cell; palette maps chars to Palette token names (as in content/play.js).
export function sprite(ctx, rows, palette, x, y, cell = 2, flip = false) {
  rows.forEach((row, j) => {
    const w = row.length
    for (let i = 0; i < w; i++) {
      const token = palette[row[flip ? w - 1 - i : i]]
      if (token) rect(ctx, P.byToken[token], x + i * cell, y + j * cell, cell, cell)
    }
  })
}

function roof(ctx, cx, base, kind, rows = 10) {
  const [main, dark] = (ROOFS[kind] || ROOFS.green).map((k) => P[k])
  for (let i = 0; i < rows; i++) {
    const w = 28 - 2 * i
    const x = cx - 14 + i
    const y = base - i
    rect(ctx, P.bg, x - 1, y, w + 2, 1)
    rect(ctx, i === 0 ? dark : main, x, y, w, 1)
    if (i % 2 === 1) for (let k = 2 + (i % 4); k < w - 1; k += 4) rect(ctx, dark, x + k, y, 1, 1)
    if (i > 0) rect(ctx, P.ink, x, y, 1, 1)
  }
  rect(ctx, P.bg, cx - 14 + rows, base - rows, 28 - 2 * rows, 1)
}

export function house(ctx, cx, by, roofKind, opts = {}) {
  shadow(ctx, cx, by, 28)
  boxed(ctx, P.muted, cx - 10, by - 13, 20, 13)
  rect(ctx, P.dim, cx + 6, by - 13, 4, 13)
  if (opts.chimney) boxed(ctx, P.dim, cx + 5, by - 25, 3, 7)
  roof(ctx, cx, by - 14, roofKind)
  boxed(ctx, opts.glow ? P.gold : P.greenInk, cx - 3, by - 8, 6, 8)
  if (opts.glow) rect(ctx, P.ink, cx - 1, by - 6, 2, 6)
  else rect(ctx, P.gold, cx + 1, by - 4, 1, 1)
  boxed(ctx, P.green, cx - 8, by - 11, 3, 3)
  rect(ctx, P.ink, cx - 8, by - 11, 1, 1)
  if (opts.twoWindows) {
    boxed(ctx, P.green, cx + 5, by - 11, 3, 3)
    rect(ctx, P.ink, cx + 5, by - 11, 1, 1)
  }
}

export function signpost(ctx, x, by) {
  rect(ctx, P.bg, x - 1, by - 11, 3, 11)
  rect(ctx, P.dim, x, by - 10, 1, 10)
  boxed(ctx, P.gold, x - 5, by - 13, 11, 4)
  rect(ctx, P.dim, x - 3, by - 12, 7, 1)
}

function flag(ctx, x, top, color, t) {
  rect(ctx, P.soft, x, top, 1, 10)
  const wave = Math.floor(t * 4) % 2
  rect(ctx, color, x + 1, top, 5, 1)
  rect(ctx, color, x + 1, top + 1, 6 + wave, 2)
  rect(ctx, color, x + 1, top + 3, 5 - wave, 1)
}

export function tower(ctx, cx, by, t, lit) {
  shadow(ctx, cx, by, 20)
  boxed(ctx, P.muted, cx - 7, by - 28, 14, 28)
  rect(ctx, P.dim, cx + 4, by - 28, 3, 28)
  for (let y = by - 24, row = 0; y < by - 3; y += 5, row++) {
    rect(ctx, P.dim, cx - 7, y, 11, 1)
    rect(ctx, P.dim, cx - 4 + (row % 2) * 4, y + 1, 1, 4)
  }
  for (let k = 0; k < 3; k++) boxed(ctx, P.muted, cx - 7 + k * 5, by - 32, 4, 3)
  rect(ctx, P.bg, cx - 1, by - 22, 3, 6)
  rect(ctx, lit ? P.gold : P.greenInk, cx, by - 21, 1, 4)
  rect(ctx, P.bg, cx - 4, by - 10, 8, 10)
  rect(ctx, P.gold, cx - 1, by - 6, 2, 2)
  rect(ctx, P.gold, cx - 1, by - 4, 1, 2)
  flag(ctx, cx, by - 42, P.gold, t)
}

function coneTower(ctx, x, by, t) {
  boxed(ctx, P.soft, x, by - 30, 9, 30)
  rect(ctx, P.muted, x + 6, by - 30, 3, 30)
  rect(ctx, P.bg, x + 3, by - 22, 3, 4)
  rect(ctx, P.green, x + 4, by - 21, 1, 2)
  for (let i = 0; i < 6; i++) {
    const w = 13 - 2 * i
    rect(ctx, P.bg, x - 3 + i, by - 31 - i, w + 2, 1)
    rect(ctx, i < 1 ? P.greenDeep : P.green, x - 2 + i, by - 31 - i, w, 1)
  }
  flag(ctx, x + 4, by - 46, P.gold, t)
}

export function castle(ctx, cx, by, t) {
  shadow(ctx, cx, by, 44)
  boxed(ctx, P.soft, cx - 12, by - 22, 24, 22)
  rect(ctx, P.muted, cx + 8, by - 22, 4, 22)
  for (let k = 0; k < 4; k++) boxed(ctx, P.soft, cx - 12 + k * 7, by - 25, 3, 2)
  coneTower(ctx, cx - 21, by, t)
  coneTower(ctx, cx + 12, by, t + 0.3)
  rect(ctx, P.bg, cx - 6, by - 13, 12, 13)
  for (let k = 0; k < 5; k++) rect(ctx, P.edge, cx - 5 + k * 2, by - 12, 1, 12)
  rect(ctx, P.edge, cx - 5, by - 8, 10, 1)
  boxed(ctx, P.green, cx - 2, by - 20, 4, 5)
  rect(ctx, P.gold, cx - 1, by - 18, 2, 1)
}

export function cave(ctx, cx, by, t, awake) {
  shadow(ctx, cx, by, 44)
  for (let i = 0; i < 12; i++) {
    const f = i / 12
    const w = Math.round(42 * Math.sqrt(1 - f * f))
    const x = cx - Math.floor(w / 2)
    const y = by - 2 - i * 2
    rect(ctx, P.bg, x - 1, y, w + 2, 2)
    rect(ctx, i % 3 === 0 ? P.edge : P.dim, x, y, w, 2)
    if (i > 2) rect(ctx, P.muted, x + 2, y, Math.max(1, Math.floor(w / 5)), 1)
  }
  rect(ctx, P.bg, cx - 6, by - 26, 12, 1)
  for (let i = 0; i < 6; i++) {
    const w = Math.round(16 * Math.sqrt(1 - (i / 6) ** 2))
    rect(ctx, P.bg, cx - Math.floor(w / 2), by - 2 - i * 2, w, 2)
  }
  if (awake && Math.floor(t * 1.5) % 4 !== 0) {
    rect(ctx, P.gold, cx - 4, by - 7, 2, 1)
    rect(ctx, P.gold, cx + 2, by - 7, 2, 1)
  }
}

export function chest(ctx, cx, by, open) {
  shadow(ctx, cx, by, 24)
  boxed(ctx, P.edge, cx - 10, by - 4, 20, 4)
  rect(ctx, P.dim, cx - 10, by - 4, 20, 1)
  boxed(ctx, P.dim, cx - 7, by - 13, 14, 9)
  rect(ctx, P.gold, cx - 7, by - 10, 14, 1)
  rect(ctx, P.gold, cx - 7, by - 13, 1, 9)
  rect(ctx, P.gold, cx + 6, by - 13, 1, 9)
  if (open) {
    rect(ctx, P.green, cx - 6, by - 13, 12, 3)
    rect(ctx, P.ink, cx - 3, by - 13, 2, 1)
    boxed(ctx, P.dim, cx - 7, by - 20, 14, 3)
    rect(ctx, P.gold, cx - 7, by - 19, 14, 1)
  } else {
    boxed(ctx, P.dim, cx - 7, by - 17, 14, 4)
    rect(ctx, P.gold, cx - 7, by - 15, 14, 1)
    boxed(ctx, P.gold, cx - 1, by - 14, 2, 3)
  }
}

export function smithy(ctx, cx, by, t, active) {
  house(ctx, cx - 4, by, 'gold', { chimney: true })
  rect(ctx, P.bg, cx + 9, by - 7, 11, 4)
  rect(ctx, P.edge, cx + 10, by - 6, 9, 2)
  rect(ctx, P.muted, cx + 10, by - 6, 9, 1)
  rect(ctx, P.bg, cx + 12, by - 3, 5, 3)
  rect(ctx, P.edge, cx + 13, by - 3, 3, 2)
  if (active && Math.floor(t * 6) % 2) {
    rect(ctx, P.gold, cx + 14, by - 10, 1, 1)
    rect(ctx, P.ink, cx + 17, by - 9, 1, 1)
    rect(ctx, P.gold, cx + 11, by - 9, 1, 1)
  }
}

export function tree(ctx, x, by) {
  rect(ctx, SHADOW, x - 5, by - 1, 10, 2)
  rect(ctx, P.bg, x - 2, by - 5, 4, 5)
  rect(ctx, P.dim, x - 1, by - 5, 2, 5)
  const rows = [4, 8, 10, 12, 12, 12, 10, 6]
  rows.forEach((w, j) => {
    const y = by - 14 + j
    const x0 = x - w / 2
    rect(ctx, P.bg, x0 - 1, y, w + 2, 1)
    rect(ctx, j > 5 ? P.greenWash : P.greenDeep, x0, y, w, 1)
    if (j > 2 && j < 6) rect(ctx, P.greenWash, x0 + w - 3, y, 3, 1)
    if (j > 0 && j < 4) rect(ctx, P.green, x0 + 2, y, 2, 1)
  })
}

export function rock(ctx, x, by) {
  rect(ctx, P.bg, x - 4, by - 5, 8, 5)
  rect(ctx, P.edge, x - 3, by - 4, 6, 4)
  rect(ctx, P.dim, x - 3, by - 4, 3, 1)
}

export function ground(ctx, w, h, rng) {
  rect(ctx, P.panel, 0, 0, w, h)
  for (let y = 0; y < h; y += 8) for (let x = 0; x < w; x += 8) rect(ctx, P.rule, x, y, 1, 1)
  for (let i = 0; i < 160; i++) {
    const x = Math.floor(rng() * w)
    const y = Math.floor(rng() * h)
    rect(ctx, P.greenWash, x, y, 1, 1)
    rect(ctx, P.greenWash, x + 2, y, 1, 1)
    rect(ctx, P.greenWash, x + 1, y + 1, 1, 1)
  }
}

export function road(ctx, poly, kind) {
  const [edge, fill, dot] = kind === 'rework' ? [P.edge, P.rule, P.gold] : [P.greenDeep, P.greenWash, P.green]
  for (const pass of [0, 1]) {
    for (let i = 0; i < poly.length - 1; i++) {
      const [a, b] = [poly[i], poly[i + 1]]
      const pad = pass === 0 ? 4 : 3
      rect(ctx, pass === 0 ? edge : fill, Math.min(a.x, b.x) - pad, Math.min(a.y, b.y) - pad,
        Math.abs(a.x - b.x) + pad * 2, Math.abs(a.y - b.y) + pad * 2)
    }
  }
  for (let i = 0; i < poly.length - 1; i++) {
    const [a, b] = [poly[i], poly[i + 1]]
    const len = Math.abs(a.x - b.x) + Math.abs(a.y - b.y)
    for (let d = 6; d < len; d += 8) {
      const f = d / len
      rect(ctx, dot, a.x + (b.x - a.x) * f - 1, a.y + (b.y - a.y) * f - 1, 2, 2)
    }
  }
}

export function pixelText(ctx, text, x, y, color) {
  ctx.font = '8px Silkscreen, monospace'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillStyle = P.bg
  for (const [dx, dy] of [[-1, 0], [1, 0], [0, -1], [0, 1], [1, 1]]) ctx.fillText(text, x + dx, y + dy)
  ctx.fillStyle = color
  ctx.fillText(text, x, y)
}
