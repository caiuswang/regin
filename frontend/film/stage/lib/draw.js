const LEVELS = [0, 1.5, 3, 6, 12, 24, 48, 96]

// Depth of field for 2D layers: everything drawn at one blur radius shares a
// bucket, and each bucket is blurred once. Wide buckets render at reduced
// resolution, which the blur hides.
export class Depth {
  constructor(W, H) {
    this.W = W
    this.H = H
    this.buckets = LEVELS.map((blur) => {
      const s = blur >= 24 ? 4 : blur >= 6 ? 2 : 1
      const w = Math.ceil(W / s), h = Math.ceil(H / s)
      const cv = new OffscreenCanvas(w, h)
      const tmp = blur ? new OffscreenCanvas(w, h) : null
      return { blur, s, cv, ctx: cv.getContext('2d'), tmp, tctx: tmp?.getContext('2d'), used: false }
    })
  }

  begin() {
    for (const b of this.buckets) {
      if (!b.used) continue
      b.ctx.setTransform(1, 0, 0, 1, 0, 0)
      b.ctx.clearRect(0, 0, b.cv.width, b.cv.height)
      b.used = false
    }
  }

  at(blurPx) {
    let best = this.buckets[0]
    for (const b of this.buckets) {
      if (Math.abs(Math.log2(1 + b.blur) - Math.log2(1 + blurPx)) < Math.abs(Math.log2(1 + best.blur) - Math.log2(1 + blurPx))) best = b
    }
    const c = best.ctx
    if (!best.used) {
      best.used = true
      c.globalCompositeOperation = 'lighter'
    }
    // Buckets outlive a frame and are shared by every world, so text state is
    // reset here: a caller that centred its text must not shift the next one.
    c.setTransform(1 / best.s, 0, 0, 1 / best.s, 0, 0)
    c.globalAlpha = 1
    c.filter = 'none'
    c.textAlign = 'left'
    c.textBaseline = 'alphabetic'
    c.letterSpacing = '0px'
    return c
  }

  flush(target, composite = 'lighter') {
    target.save()
    target.setTransform(1, 0, 0, 1, 0, 0)
    target.globalCompositeOperation = composite
    target.globalAlpha = 1
    for (const b of this.buckets) {
      if (!b.used) continue
      let src = b.cv
      if (b.blur) {
        b.tctx.setTransform(1, 0, 0, 1, 0, 0)
        b.tctx.clearRect(0, 0, b.tmp.width, b.tmp.height)
        b.tctx.filter = `blur(${(b.blur / b.s).toFixed(2)}px)`
        b.tctx.drawImage(b.cv, 0, 0)
        b.tctx.filter = 'none'
        src = b.tmp
      }
      target.drawImage(src, 0, 0, this.W, this.H)
    }
    target.restore()
    this.begin()
  }
}

let depthSingleton = null
export function depthFor(W, H) {
  if (!depthSingleton || depthSingleton.W !== W) depthSingleton = new Depth(W, H)
  return depthSingleton
}

export function rgba([r, g, b], a = 1) {
  return `rgba(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)}, ${Math.max(0, Math.min(1, a)).toFixed(4)})`
}

export function glowPath(ctx, path, color, width, { alpha = 1, halo = 1, core = 1 } = {}) {
  ctx.save()
  ctx.globalCompositeOperation = 'lighter'
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  const passes = [[width * 9, 0.035 * halo], [width * 3.2, 0.14 * halo], [width, 0.85 * core]]
  for (const [w, a] of passes) {
    ctx.lineWidth = w
    ctx.strokeStyle = rgba(color, a * alpha)
    ctx.stroke(path)
  }
  ctx.restore()
}

export function dot(ctx, x, y, r, color, alpha = 1) {
  const g = ctx.createRadialGradient(x, y, 0, x, y, r)
  g.addColorStop(0, rgba(color, alpha))
  g.addColorStop(0.25, rgba(color, alpha * 0.55))
  g.addColorStop(1, rgba(color, 0))
  ctx.fillStyle = g
  ctx.beginPath()
  ctx.arc(x, y, r, 0, Math.PI * 2)
  ctx.fill()
}

export function font(ctx, { family = 'PingFang SC', weight = 300, size, tracking = 0 }) {
  ctx.font = `${weight} ${size}px "${family}"`
  ctx.letterSpacing = `${(tracking * size).toFixed(2)}px`
}

// Canvas letter-spacing also trails the last glyph; centring on the visible
// ink means shifting right by half of one spacing.
export function textCentered(ctx, text, x, y, { size, tracking = 0 }) {
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(text, x + (tracking * size) / 2, y)
}
