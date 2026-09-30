import { rgba } from './draw.js'
import { add, scale } from './math.js'

// Text standing in 3D: each glyph is placed by projecting its baseline ends
// and its top, and drawn with the affine that maps its box onto them. Close
// enough to true perspective for glyphs a few degrees wide.
export function layoutLine(ctx, text, { family, weight = 400, px = 200, tracking = 0 }) {
  ctx.font = `${weight} ${px}px "${family}"`
  ctx.letterSpacing = '0px'
  const glyphs = []
  let x = 0
  for (const ch of text) {
    const w = ctx.measureText(ch).width
    glyphs.push({ ch, x, w })
    x += w + tracking * px
  }
  return { glyphs, width: x - tracking * px, px, family, weight }
}

export function glyphFrame(cam, origin, dir, unit, g, height) {
  const a = add(origin, scale(dir, g.x * unit))
  const b = add(origin, scale(dir, (g.x + g.w) * unit))
  const top = add(a, [0, height, 0])
  const pa = cam.project(a), pb = cam.project(b), pt = cam.project(top)
  if (!pa || !pb || !pt) return null
  return { pa, pb, pt, z: (pa.z + pb.z) / 2 }
}

export function drawGlyph(ctx, g, fr, layout, color, alpha, { mirror = false } = {}) {
  const sx = (fr.pb.x - fr.pa.x) / g.w
  const skew = (fr.pb.y - fr.pa.y) / g.w
  const sy = (fr.pa.y - fr.pt.y) / (layout.px * 0.8)
  ctx.save()
  ctx.setTransform(ctx.getTransform().multiply(new DOMMatrix([sx, skew, 0, mirror ? -sy : sy, fr.pa.x, fr.pa.y])))
  ctx.font = `${layout.weight} ${layout.px}px "${layout.family}"`
  ctx.letterSpacing = '0px'
  ctx.textBaseline = 'alphabetic'
  ctx.textAlign = 'left'
  ctx.fillStyle = rgba(color, alpha)
  ctx.fillText(g.ch, 0, 0)
  ctx.restore()
}
