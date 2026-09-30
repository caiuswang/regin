import { at } from '../lib/anim.js'
import { depthFor, rgba } from '../lib/draw.js'
import { add, clamp, scale, smooth } from '../lib/math.js'
import { cameraFrom, coc } from '../lib/scene3d.js'
import { drawVoid } from './void.js'

// Code on a sheet of dark glass, seen at an angle: the three beats of an
// agent's loop (read, edit, test) each get one close shot of it.
const SOURCE = `from regin.trace import span_of
from regin.memory import recall


def explain(failure: Failure) -> Explanation:
    """Every failure names its cause."""
    span = span_of(failure.step)
    cause = span.first_error() or span.parent.first_error()
    if cause is None:
        return Explanation.unknown(failure, hint=span.summary())
    lesson = recall(cause.signature)
    return Explanation(
        failure=failure,
        cause=cause,
        where=span.path,
        seen_before=lesson is not None,
        fix=lesson.rule if lesson else None,
    )


def attribute(defect: Defect) -> Feature:
    run = defect.caught_in
    while run.parent is not None:
        run = run.parent
    return run.feature


def verify(task: Task) -> Verdict:
    results = [check(item) for item in task.acceptance]
    failed = [r for r in results if not r.passed]
    return Verdict(passed=not failed, failed=failed)`

const DIFF = [
  { kind: 'ctx', text: '    if cause is None:' },
  { kind: 'del', text: '        return Explanation.unknown(failure)' },
  { kind: 'add', text: '        return Explanation.unknown(failure, hint=span.summary())' },
  { kind: 'ctx', text: '    lesson = recall(cause.signature)' },
]

const TESTS = [
  'test_failure_names_its_cause', 'test_unknown_cause_carries_a_hint', 'test_recalled_lesson_suggests_fix',
  'test_parent_error_is_the_cause', 'test_defect_attributed_to_feature', 'test_verdict_lists_failed_items',
  'test_span_path_is_stable', 'test_lesson_rule_is_imperative', 'test_retry_keeps_context',
  'test_verifier_is_independent', 'test_trace_replays_every_step', 'test_cost_is_attributed',
  'test_no_step_without_a_span', 'test_memory_recall_is_ranked', 'test_explanation_serializes',
]

const KEYWORDS = /^(def|return|if|is|None|not|for|in|from|import|while|or|else|class)$/
const C = {
  base: [150, 176, 204], kw: [128, 216, 255], str: [236, 204, 150], fn: [214, 236, 255], dim: [90, 110, 134],
  hot: [240, 250, 255], del: [255, 92, 112], add: [120, 240, 200], pass: [110, 232, 190],
}

function tokens(line) {
  const out = []
  const re = /("""[^"]*"""|"[^"]*"|[A-Za-z_][A-Za-z0-9_]*|\s+|.)/g
  let m, col = 0
  while ((m = re.exec(line))) {
    const s = m[0]
    let kind = 'base'
    if (/^\s+$/.test(s)) kind = 'space'
    else if (s.startsWith('"')) kind = 'str'
    else if (KEYWORDS.test(s)) kind = 'kw'
    else if (/^[A-Z]/.test(s)) kind = 'fn'
    out.push({ s, col, kind })
    col += s.length
  }
  return out
}

class Sheet {
  constructor(f, p, cam) {
    const yaw = ((p.yaw ?? 18) * Math.PI) / 180, tilt = ((p.tilt ?? 32) * Math.PI) / 180
    this.ex = [Math.cos(yaw), 0, Math.sin(yaw)]
    this.ey = [Math.sin(tilt) * Math.sin(yaw), -Math.cos(tilt), -Math.sin(tilt) * Math.cos(yaw)]
    this.origin = p.origin ?? [-3.2, 1.6, 0]
    this.charW = p.charW ?? 0.118
    this.lineH = p.lineH ?? 0.26
    this.cam = cam
    this.f = f
  }

  at(col, row) { return add(add(this.origin, scale(this.ex, col * this.charW)), scale(this.ey, row * this.lineH)) }

  depthOf(col, row) { return this.cam.project(this.at(col, row))?.z ?? 5 }

  draw(depth, text, col, row, color, alpha, { focus, aperture, px = 120, weight = 400 }) {
    const n = text.length
    const a = this.cam.project(this.at(col, row)), b = this.cam.project(this.at(col + n, row))
    const t = this.cam.project(add(this.at(col, row), scale(this.ey, -this.lineH * 0.62)))
    if (!a || !b || !t) return
    const ctx = depth.at(coc(this.f, a.z, focus, aperture, 40))
    ctx.save()
    ctx.font = `${weight} ${px}px "Menlo"`
    ctx.letterSpacing = '0px'
    const w = ctx.measureText(text).width
    ctx.setTransform(ctx.getTransform().multiply(new DOMMatrix([(b.x - a.x) / w, (b.y - a.y) / w, (t.x - a.x) / (-px * 0.72), (t.y - a.y) / (-px * 0.72), a.x, a.y])))
    ctx.fillStyle = rgba(color, alpha)
    ctx.textBaseline = 'alphabetic'
    ctx.fillText(text, 0, 0)
    ctx.restore()
  }

  // A long run is drawn in chunks so each chunk takes the blur of its own depth.
  run(depth, text, col, row, color, alpha, style, chunk = 8) {
    for (let i = 0; i < text.length; i += chunk) {
      const piece = text.slice(i, i + chunk)
      if (piece.trim()) this.draw(depth, piece, col + i, row, color, alpha, style)
    }
  }

  band(depth, row, cols, color, alpha, { focus, aperture }) {
    const pts = [this.at(-1.5, row + 0.3), this.at(cols, row + 0.3), this.at(cols, row - 0.85), this.at(-1.5, row - 0.85)].map((q) => this.cam.project(q))
    if (pts.some((q) => !q)) return
    const ctx = depth.at(coc(this.f, pts[0].z, focus, aperture, 40))
    ctx.save()
    ctx.globalCompositeOperation = 'lighter'
    ctx.fillStyle = rgba(color, alpha)
    ctx.beginPath()
    pts.forEach((q, i) => (i ? ctx.lineTo(q.x, q.y) : ctx.moveTo(q.x, q.y)))
    ctx.closePath()
    ctx.fill()
    ctx.restore()
  }
}

function drawRead(f, depth, sheet, p, style) {
  const lines = SOURCE.split('\n')
  const reading = at(p.reading ?? { from: 3, to: 18, t0: 0, t1: f.dur, ease: 'linear' }, f.t)
  lines.forEach((line, row) => {
    const d = row - reading
    const lit = Math.exp(-(d * d) / 0.35)
    const read = row < reading ? 0.35 : 0
    for (const tk of tokens(line)) {
      if (tk.kind === 'space') continue
      const base = C[tk.kind] ?? C.base
      const col = lit > 0.5 ? C.hot : base
      sheet.draw(depth, tk.s, tk.col, row, col, 0.42 + read * 0.4 + lit * 0.55, style)
    }
  })
  const r = Math.round(reading)
  sheet.band(depth, reading, 64, [80, 170, 255], 0.07, style)
  sheet.band(depth, r, 64, [120, 200, 255], 0.03, style)
}

function drawEdit(f, depth, sheet, p, style) {
  const t0 = p.typeStart ?? 0.35, cps = p.cps ?? 34
  DIFF.forEach((l, row) => {
    const gutter = l.kind === 'del' ? '-' : l.kind === 'add' ? '+' : ' '
    if (l.kind === 'del') {
      const fade = 1 - 0.7 * smooth(0.1, 0.7, f.t)
      sheet.draw(depth, gutter, -1.6, row, C.del, 0.9 * fade, style)
      sheet.run(depth, l.text, 0, row, C.del, 0.75 * fade, style)
      const strike = clamp((f.t - 0.1) / 0.5)
      sheet.band(depth, row - 0.25, 4 + l.text.trimEnd().length * strike, [255, 60, 90], 0.08 * fade, style)
      return
    }
    if (l.kind === 'add') {
      const n = Math.floor(clamp((f.t - t0) * cps, 0, l.text.length))
      const shown = l.text.slice(0, n)
      sheet.band(depth, row, 64, [60, 230, 180], 0.05, style)
      sheet.draw(depth, gutter, -1.6, row, C.add, 0.95, style)
      sheet.run(depth, shown, 0, row, C.add, 0.95, style)
      if (Math.floor(f.t / 0.5) % 2 === 0 || n < l.text.length) sheet.draw(depth, '▏', n, row, [230, 255, 245], 1, style)
      return
    }
    for (const tk of tokens(l.text)) if (tk.kind !== 'space') sheet.draw(depth, tk.s, tk.col, row, C[tk.kind] ?? C.base, 0.5, style)
  })
}

function drawTests(f, depth, sheet, p, style) {
  const rate = p.rate ?? 6.5
  const shown = Math.min(TESTS.length, Math.floor(f.t * rate) + 1)
  const scroll = Math.max(0, shown - (p.window ?? 9))
  for (let i = 0; i < shown; i++) {
    const row = i - scroll
    const age = f.t - i / rate
    const flash = 1 + 0.9 * Math.exp(-age * 5)
    const path = `tests/trace/test_explain.py::${TESTS[i]}`
    sheet.run(depth, path, 0, row, C.base, 0.55, style)
    sheet.draw(depth, 'PASSED', path.length + 1, row, C.pass, Math.min(1, 0.85 * flash), { ...style, weight: 700 })
  }
  if (shown === TESTS.length && f.t * rate > TESTS.length + 1) {
    const done = smooth(0, 0.4, f.t - (TESTS.length + 1) / rate)
    sheet.run(depth, `======== ${TESTS.length} passed in 1.83s ========`, 0, TESTS.length - scroll + 0.6, C.pass, done, { ...style, weight: 700 })
  }
}

// The lens follows the action: the line being read, the line being typed,
// the newest test result.
function focusCell(f, p) {
  if (p.mode === 'read') return [16, at(p.reading ?? { from: 3, to: 18, t0: 0, t1: f.dur, ease: 'linear' }, f.t)]
  if (p.mode === 'edit') return [18, 2]
  const shown = Math.min(TESTS.length, Math.floor(f.t * (p.rate ?? 6.5)) + 1)
  return [30, shown - 1 - Math.max(0, shown - (p.window ?? 9))]
}

export default {
  grade: 'code',
  render(f, stage) {
    const p = f.p
    drawVoid(f, stage, { dust: 0.04, top: [0.003, 0.006, 0.012], bottom: [0.001, 0.002, 0.004] })
    const cam = cameraFrom(f, p.cam)
    const depth = depthFor(f.W, f.H)
    const sheet = new Sheet(f, p, cam)
    const style = { focus: p.focus ?? sheet.depthOf(...focusCell(f, p)), aperture: p.aperture ?? 0.9 }
    if (p.mode === 'read') drawRead(f, depth, sheet, p, style)
    else if (p.mode === 'edit') drawEdit(f, depth, sheet, p, style)
    else if (p.mode === 'tests') drawTests(f, depth, sheet, p, style)
    else throw new Error(`code: unknown mode ${p.mode}`)
    depth.flush(stage.emit)
    return { emitGain: p.gain ?? 1.45 }
  },
}
