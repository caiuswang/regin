const BASE = {
  exposure: 0,
  emitGain: 1,
  overGain: 1,
  threshold: 0.85,
  knee: 0.5,
  bloom: 1.1,
  bloomRadius: 1.0,
  bloomTint: [1, 0.97, 0.95],
  streak: 0.12,
  streakTint: [0.75, 0.85, 1.0],
  ca: 0.0022,
  vignette: 0.5,
  grain: 0.032,
  saturation: 1.0,
  contrast: 1.06,
  wb: [1, 1, 1],
  lift: [0, 0, 0],
  gamma: [1, 1, 1],
  gain: [1, 1, 1],
  fade: 1,
}

// Teal shadows and cool highlights at night; the warm grade is kept for the
// human light and for dawn, so the two temperatures carry the story.
export const GRADES = {
  night: { wb: [0.94, 1.0, 1.1], lift: [0.0, 0.006, 0.014], gain: [0.98, 1.0, 1.03] },
  card: { bloom: 0.9, streak: 0.06, vignette: 0.6, wb: [1.0, 0.99, 0.97], lift: [0.004, 0.004, 0.006] },
  warm: { wb: [1.08, 1.0, 0.9], lift: [0.008, 0.004, 0.0], gain: [1.03, 1.0, 0.95], streakTint: [1.0, 0.7, 0.45] },
  alarm: { wb: [1.12, 0.92, 0.9], saturation: 0.75, lift: [0.012, 0.0, 0.0], gain: [1.02, 0.94, 0.94], contrast: 1.12 },
  dawn: { wb: [1.03, 1.0, 0.97], lift: [0.006, 0.004, 0.01], gain: [1.02, 1.0, 0.98], streakTint: [1.0, 0.8, 0.6], bloom: 1.2 },
  code: { wb: [0.92, 1.0, 1.08], lift: [0.0, 0.008, 0.016], streak: 0.08 },
}

export function gradeFor(name, overrides = {}) {
  const preset = GRADES[name]
  if (!preset) throw new Error(`unknown grade "${name}"`)
  return { ...BASE, ...preset, ...overrides }
}
