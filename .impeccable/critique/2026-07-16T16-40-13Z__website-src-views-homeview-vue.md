---
target: website/src/views/HomeView.vue
total_score: 31
p0_count: 0
p1_count: 1
timestamp: 2026-07-16T16-40-13Z
slug: website-src-views-homeview-vue
---
---
target: website/src/views/HomeView.vue
total_score: 31
p0_count: 0
p1_count: 1
timestamp: 2026-07-16T16-39-58Z
slug: website-src-views-homeview-vue
---
Method: dual-agent (A: a524a923cb32f74d6 · B: aeb9a6f4d0c7f4b2e)

# Re-critique — regin homepage (post fix-pass, commit 7b06b40)

## Design Health Score — 31/40 (Good; was 30)

Status 3 · Real-world match 4 · Control 3 · Consistency 3 · Error prevention 3 · Recognition 3 · Flexibility 3 · Aesthetic 3 · Error recovery 2 · Help/docs 4.

## Verified fixed since last run
- Reduced-motion gate: measured `animationName: none` under reduce (was animating).
- Mobile proof: crop variants served via <picture> (legible), payload 264KB mobile / 811KB desktop (was 1.14MB both).
- Quickstart: wraps, copy button works (clipboard verified), no truncation.
- Footer targets 46px; duo-card stripe gone; eyebrow-chip gone (tagline is now the H1); measure caps applied; in-page detector: 10 anti-patterns → 2 (line-length ~114 + single-font).

## New / surviving issues
1. **[P1] Pillar column parity bug.** `.pillar:nth-of-type(even)` counts the section-head div, so pillars 1 & 3 flip and the `order`-swap puts artifacts in the narrow 5fr track — memory screenshot renders 422×258 (illegible), warn-log cramped, while pillar 2's four chips float in the 616px column. Fix: `:nth-child(even of .pillar)` + explicit column swap so artifacts always take the 7fr track.
2. **[P2] Proof artifacts fail their own audit (copy):** (a) stats caption claims "screenshots nearby already show slightly different totals" — false for the desktop shot, which matches exactly; (b) log says "2 violation(s)" but lists one; (c) caption says "refusal" while the log says `(warn)`.
3. **[P2] No CTA at the end** — the fully-persuaded reader lands on the footer with nothing to click.
4. **[P3] Image pipeline** (PNG→AVIF/WebP ≈ −60%; og:image missing — needs a deployed URL), copy-button 32px + no aria-live announce, theme toggle exposes no pressed state, mobile menu has no scrim, Sensors card punctuation, "Introduction" nav label vs brand register tension.
5. Detector advisories (23 design-system-*) grade the website CSS against DESIGN.md's 6-role type ramp — intermediate sizes are deliberate; advisory-level, mostly acceptable; `single-font` remains a declared identity (open for a future typeset pass).

## Peak-end
Peak: the self-referential trace (now captioned with its own 30/40 critique — disarming). End: honest but action-less.

## Provocative questions (A)
1. Why isn't the website itself under regin's rule engines — and why not say so on the page as the ultimate dogfood proof?
2. Why is the proof a picture of an inspectable artifact instead of a link to a read-only live trace?
3. Where's the before/after that makes "my agent drifts" visceral rather than asserted?
4. Is the homepage a docs "Introduction" or a brand landing page? Nav label and register quietly disagree.
