import { clamp, easeInOut, easeOut, easeIn } from './math.js'

const EASES = { inOut: easeInOut, out: easeOut, in: easeIn, linear: (x) => clamp(x) }

// A storyboard value is either a constant or a move: { from, to, t0, t1, ease }.
// Arrays interpolate element-wise, so cameras and colours move the same way.
export function at(v, t) {
  if (v == null || typeof v !== 'object' || Array.isArray(v) || !('from' in v)) return v
  const k = (EASES[v.ease ?? 'inOut'])(clamp((t - (v.t0 ?? 0)) / ((v.t1 ?? 1) - (v.t0 ?? 0))))
  if (Array.isArray(v.from)) return v.from.map((a, i) => a + (v.to[i] - a) * k)
  return v.from + (v.to - v.from) * k
}
