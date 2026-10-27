import { MAP, LEVELS, ROADS, QUEST } from '../../content/quest.js'
import { BUG_PALETTE, BUG_ROWS, ROBOT_FRAME_1, ROBOT_FRAME_2, ROBOT_PALETTE } from '../../content/play.js'
import { P, loadPalette, sprite, house, signpost, tower, castle, cave, chest, smithy, tree, rock, ground, road, pixelText } from './art.js'

const T = MAP.tile
export const VIEW = { width: MAP.cols * T, height: MAP.rows * T }
const SPEED = 70
const ROBOT = [ROBOT_FRAME_1, ROBOT_FRAME_2]

export const levelById = Object.fromEntries(LEVELS.map((l) => [l.id, l]))

function seeded(seed) {
  let s = seed
  return () => {
    s = (s * 1664525 + 1013904223) % 4294967296
    return s / 4294967296
  }
}

export function center(pos) {
  return { x: pos.x * T + 8, y: pos.y * T + 8 }
}

export function route(a, b) {
  const p = center(a)
  const q = center(b)
  const corner = Math.abs(q.x - p.x) >= Math.abs(q.y - p.y) ? { x: q.x, y: p.y } : { x: p.x, y: q.y }
  return [p, corner, q]
}

function lengthOf(poly) {
  let n = 0
  for (let i = 0; i < poly.length - 1; i++) n += Math.abs(poly[i + 1].x - poly[i].x) + Math.abs(poly[i + 1].y - poly[i].y)
  return n
}

function pointAt(poly, t) {
  let left = lengthOf(poly) * t
  for (let i = 0; i < poly.length - 1; i++) {
    const [a, b] = [poly[i], poly[i + 1]]
    const seg = Math.abs(b.x - a.x) + Math.abs(b.y - a.y)
    if (left <= seg && seg > 0) {
      const f = left / seg
      return { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f, dx: Math.sign(b.x - a.x) }
    }
    left -= seg
  }
  const end = poly[poly.length - 1]
  return { x: end.x, y: end.y, dx: 0 }
}

const DECOR = (() => {
  const rng = seeded(7)
  const items = []
  for (let i = 0; i < 52; i++) {
    items.push({ x: 8 + Math.floor(rng() * (VIEW.width - 16)), y: 18 + Math.floor(rng() * (VIEW.height - 22)), rock: i % 6 === 0 })
  }
  return items.sort((a, b) => a.y - b.y)
})()

function nearRoad(x, y, polys) {
  return polys.some((poly) => poly.slice(1).some((b, i) => {
    const a = poly[i]
    return x >= Math.min(a.x, b.x) - 9 && x <= Math.max(a.x, b.x) + 9 && y >= Math.min(a.y, b.y) - 3 && y <= Math.max(a.y, b.y) + 17
  }))
}

function nearLevel(x, y, positions) {
  return LEVELS.some((l) => {
    const c = center(positions[l.id])
    return Math.abs(c.x - x) < 30 && y > c.y - 34 && y < c.y + 26
  })
}

export class QuestEngine {
  constructor(canvas, positions, hooks) {
    loadPalette(canvas)
    this.ctx = canvas.getContext('2d')
    this.positions = positions
    this.hooks = hooks
    this.ground = document.createElement('canvas')
    this.ground.width = VIEW.width
    this.ground.height = VIEW.height
    ground(this.ground.getContext('2d'), VIEW.width, VIEW.height, seeded(3))
    this.time = 0
    this.playing = false
    this.frame = null
    this.restart()
  }

  restart() {
    this.step = -1
    this.wait = 0
    this.hero = { at: 'spec', from: null, to: null, t: 0, flip: false }
    this.bug = false
    this.chestOpen = false
    this.round = 1
    this.particles = []
    this.floaters = []
    this.victory = 0
    this.hooks.onReset?.()
    this.hooks.onRound?.(1)
    this.advance()
  }

  play() {
    if (this.playing) return
    this.playing = true
    let last = performance.now()
    const tick = (now) => {
      this.update(Math.min(0.05, (now - last) / 1000))
      last = now
      this.draw()
      if (this.playing) this.frame = requestAnimationFrame(tick)
    }
    this.frame = requestAnimationFrame(tick)
  }

  pause() {
    this.playing = false
    cancelAnimationFrame(this.frame)
  }

  advance() {
    this.step = (this.step + 1) % QUEST.length
    if (this.step === 0 && this.hero.from) return this.restart()
    const s = QUEST[this.step]
    if (!s.walk) return this.arrive(s)
    Object.assign(this.hero, { from: this.hero.at, to: s.walk, t: 0, at: null })
  }

  arrive(s) {
    if (s.log) this.hooks.onLog?.(s.log, this.round)
    if (s.say) this.hooks.onSay?.(levelById[s.say[0]], s.say[1])
    if (s.chest) this.chestOpen = true
    if (s.spawn) {
      this.bug = true
      this.burst(center(this.positions.defect), P.gold, 10)
    }
    if (s.fight) this.defeatBug()
    if (s.round) {
      this.round += 1
      this.chestOpen = false
      this.hooks.onRound?.(this.round)
      this.floater(center(this.positions.run), '+1 ROUND', P.gold)
    }
    if (s.victory) {
      this.victory = 3
      this.floater(center(this.positions.ship), 'SHIPPED', P.green)
    }
    this.wait = s.rest ?? (s.say ? 2.4 : 0.6)
  }

  defeatBug() {
    this.bug = false
    const c = center(this.positions.defect)
    this.burst(c, P.gold, 18)
    this.burst(c, P.ink, 8)
    this.floater(c, 'CHECK NAMED', P.ink)
  }

  burst(c, color, n) {
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2
      const v = 25 + (i % 3) * 15
      this.particles.push({ x: c.x, y: c.y - 6, vx: Math.cos(a) * v, vy: Math.sin(a) * v - 20, life: 0.8, color, g: 60 })
    }
  }

  floater(c, text, color) {
    this.floaters.push({ x: c.x, y: c.y - 30, text, color, life: 1.6 })
  }

  update(dt) {
    this.time += dt
    const h = this.hero
    if (h.to) {
      const poly = route(this.positions[h.from], this.positions[h.to])
      h.t += (SPEED * dt) / Math.max(1, lengthOf(poly))
      if (h.t >= 1) {
        Object.assign(h, { at: h.to, to: null })
        this.arrive(QUEST[this.step])
      }
    } else if ((this.wait -= dt) <= 0) {
      this.advance()
    }
    this.ambient(dt)
  }

  ambient(dt) {
    const run = center(this.positions.run)
    if (Math.random() < dt * 2.5) this.particles.push({ x: run.x + 7, y: run.y - 18, vx: 4, vy: -12, life: 1.4, color: P.dim, g: 0, size: 2 })
    if (this.victory > 0) {
      this.victory -= dt
      if (Math.random() < dt * 4) {
        const c = center(this.positions.ship)
        const colors = [P.green, P.gold, P.ink, P.greenHi]
        this.burst({ x: c.x + (Math.random() - 0.5) * 60, y: c.y - 40 - Math.random() * 20 }, colors[Math.floor(Math.random() * 4)], 12)
      }
    }
    for (const p of this.particles) {
      p.life -= dt
      p.x += p.vx * dt
      p.y += p.vy * dt
      p.vy += p.g * dt
    }
    this.particles = this.particles.filter((p) => p.life > 0)
    for (const f of this.floaters) {
      f.life -= dt
      f.y -= 12 * dt
    }
    this.floaters = this.floaters.filter((f) => f.life > 0)
  }

  heroPoint() {
    const h = this.hero
    if (!h.to) return { ...center(this.positions[h.at]), dx: 0 }
    return pointAt(route(this.positions[h.from], this.positions[h.to]), h.t)
  }

  draw() {
    const ctx = this.ctx
    ctx.imageSmoothingEnabled = false
    ctx.drawImage(this.ground, 0, 0)
    const roads = ROADS.map(([a, b, kind]) => ({ kind, poly: route(this.positions[a], this.positions[b]) }))
    for (const kind of ['rework', 'main']) roads.filter((r) => r.kind === kind).forEach((r) => road(ctx, r.poly, kind))
    const polys = roads.map((r) => r.poly)
    const actors = DECOR.filter((d) => !nearRoad(d.x, d.y, polys) && !nearLevel(d.x, d.y, this.positions))
      .map((d) => ({ y: d.y, draw: () => (d.rock ? rock(ctx, d.x, d.y) : tree(ctx, d.x, d.y)) }))
    for (const l of LEVELS) {
      const c = center(this.positions[l.id])
      actors.push({ y: c.y + 8, draw: () => this.drawLevel(l, c) })
    }
    actors.sort((a, b) => a.y - b.y).forEach((a) => a.draw())
    if (this.bug) this.drawBug()
    this.drawHero()
    for (const p of this.particles) {
      ctx.globalAlpha = Math.min(1, p.life * 2)
      ctx.fillStyle = p.color
      ctx.fillRect(Math.round(p.x), Math.round(p.y), p.size || 1, p.size || 1)
    }
    ctx.globalAlpha = 1
    for (const f of this.floaters) pixelText(ctx, f.text, f.x, Math.round(f.y), f.color)
  }

  drawBug() {
    const c = center(this.positions.defect)
    const wiggle = Math.floor(this.time * 6) % 2
    sprite(this.ctx, BUG_ROWS, BUG_PALETTE, c.x - 9 + wiggle, c.y + 6)
  }

  drawHero() {
    const hp = this.heroPoint()
    if (hp.dx) this.hero.flip = hp.dx < 0
    const walking = !!this.hero.to
    const frame = walking ? Math.floor(this.time * 8) % 2 : 0
    const bob = walking ? 0 : Math.floor(this.time * 2) % 2
    const front = walking ? 0 : 12
    sprite(this.ctx, ROBOT[frame], ROBOT_PALETTE, Math.round(hp.x) - 9, Math.round(hp.y) - 20 - bob + front, 2, this.hero.flip)
  }

  drawLevel(l, c) {
    const ctx = this.ctx
    const by = c.y + 8
    const here = this.hero.at === l.id
    if (l.kind === 'house') {
      house(ctx, c.x, by, l.roof)
      if (l.id === 'spec') signpost(ctx, c.x - 19, by)
    } else if (l.kind === 'library') house(ctx, c.x, by, l.roof, { twoWindows: true })
    else if (l.kind === 'forge') house(ctx, c.x, by, l.roof, { chimney: true, glow: Math.floor(this.time * 5) % 3 !== 0 })
    else if (l.kind === 'tower') tower(ctx, c.x, by, this.time, here)
    else if (l.kind === 'castle') castle(ctx, c.x, by, this.time)
    else if (l.kind === 'cave') cave(ctx, c.x, by, this.time, !this.bug)
    else if (l.kind === 'chest') chest(ctx, c.x, by, this.chestOpen)
    else if (l.kind === 'smithy') smithy(ctx, c.x, by, this.time, here)
  }
}
