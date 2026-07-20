---
target: website/src/views/HomeView.vue
total_score: 30
p0_count: 0
p1_count: 2
timestamp: 2026-07-16T16-12-57Z
slug: website-src-views-homeview-vue
---
Method: dual-agent (A: aa463d6aaca0e8d3a · B: af9fb4f05391defa3)

# Critique — regin homepage (website/src/views/HomeView.vue)

## Design Health Score — 30/40 (Good)

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | No within-page orientation on a ~4200px page; "Introduction" ≠ obviously "home" |
| 2 | Match System / Real World | 3 | "feedforward", "Sensors", "spans", "PostToolUse" land unglossed on first-timers |
| 3 | User Control and Freedom | 3 | Solid (theme persists, menu closes, no traps); GitHub CTA leaves in same tab |
| 4 | Consistency and Standards | 3 | Duo-card 3px accent stripe breaks the site's own no-stripe rule; footer targets 23px |
| 5 | Error Prevention | 2 | Quickstart truncated mid-URL at 390px, no copy button — the conversion artifact is the biggest error trap |
| 6 | Recognition Rather Than Recall | 3 | 2-mechanism → 3-layer renaming forces the reader to re-derive the mapping |
| 7 | Flexibility and Efficiency | 3 | Skip link + focus rings; missing copy affordance slows the expert path |
| 8 | Aesthetic and Minimalist Design | 4 | Genuine restraint; docked by hero green triple-hit and wordy pillar paragraphs |
| 9 | Error Recovery | 2 | No troubleshooting pointer near the quickstart |
| 10 | Help and Documentation | 4 | The site is documentation; deep links all resolve (verified) |
| **Total** | | **30/40** | **Good — address weak areas, solid foundation** |

## Anti-Patterns Verdict

**LLM assessment (A): not slop, with two demerits.** The proof architecture (a page tracing its own build session, quoting its own hook refusal) is structurally impossible for a template to fake. Demerits: the duo-card top-stripe (the banned side-stripe rotated 90°), and second-order familiarity — the visual system sits in the saturated Inter+slate+hairline docs family; the content design is what rescues it.

**Deterministic scan (B): CLI 2 findings, in-page detector 10.**
- `border-accent-on-rounded` ×2 — the duo-cards (agrees with A; genuine, also self-contradicts DESIGN.md).
- `line-length` ×3 — ~174, ~165, ~87 chars/line (paragraphs outside `article` lack a measure cap; A flagged wordiness but missed the measure gap — detector caught it).
- `icon-tile-stack` ×3 — 42px icon tile above every pillar h3 (the brand-ban "rounded icon above every heading"; A praised the pillars and missed this tell).
- `hero-eyebrow-chip` ×1 — the single kicker; accepted as the site's one named Label (DESIGN.md rule), **false positive by declared brand system**, though A independently questioned whether the tagline should be the smallest element in the hero.
- `overused-font`/`single-font` (4 hits, 1 root cause) — Inter-only; **accepted identity** per DESIGN.md ("one typeface, many weights"), identity-preservation wins.

**Browser evidence:** headless run — no human-visible overlay tab existed; injection succeeded and console output was captured. Mechanical: zero console errors, zero h-overflow at 1440/390; **payload ~1.14MB, of which the two 2880px PNGs are ~1.1MB, transferred identically at 390px (no srcset)**.

## Overall Impression

A trustworthy page with a genuinely original proof strategy that currently only works on desktop. The single biggest opportunity: make the evidence survive mobile — screenshots legible, quickstart copyable — because that is where the page's entire persuasive weight sits.

## What's Working

1. **Self-referential proof architecture** — trace of its own build, its own refusal log, its own cost chips: checkable, first-person, unfakeable.
2. **Honesty placed structurally** — "pin a commit" under the CTA, "Claude only, today" as a closing section; the highest-trust move for a skeptical-dev audience.
3. **Three pillars, three evidence shapes** in alternating rows — dodges the identical-card-grid tell.

## Priority Issues

1. **[P1] The proof collapses on mobile.** 2880px PNGs illegible at 390px and 1.1MB over slow connections (no srcset); quickstart truncated mid-URL; refusal log clips mid-word. *Why:* mobile keeps the claims but loses the evidence and degrades conversion. *Fix:* cropped mobile srcset variants, `pre-wrap` or stacked quickstart, narrower log lines. → `/impeccable adapt`
2. **[P1] Claim–evidence contradiction.** "every token attributed" (HomeProof) vs `$4.14 of $34.38 attributed` (chip). Riley reads the page's own evidence as undercutting its copy. *Fix:* soften to "every tool call attributed" or explain the denominator in the caption. → `/impeccable clarify`
3. **[P2] Reduced-motion gate fails.** Measured: `.shot-frame` scroll-timeline animation still runs under `prefers-reduced-motion: reduce` (the blanket duration override doesn't touch scroll-driven timelines). *Fix:* wrap the `@supports` block in `@media (prefers-reduced-motion: no-preference)`. → `/impeccable polish`
4. **[P2] The 2→3 model seam.** Guides/Sensors → Rules/Viewer/Lessons with no mapping sentence (the page's one working-memory bridge). *Fix:* one connective line in the pillars section-head. → `/impeccable clarify`
5. **[P2→P3] Off-system details.** Duo-card stripe (→hairline), footer links 23px (<24 gate), orphaned mini-row arrow <640px, hero green triple-hit, pillar icon-tile stack ×3, unmeasured paragraphs (~174ch), favicon green-500 off-token, no OG/Twitter meta. → `/impeccable polish`

## Persona Red Flags

**Jordan (first-timer):** "AGENT = MODEL + HARNESS" is an equation of two undefined terms read before the definition; "Guides — feedforward / Sensors — feedback" are control-theory flashcards; recovers via the hero lead and leaves through "Read the guide". Passes with jargon bruises.

**Riley (stress tester):** catches the $4.14/$34.38 vs "every token" contradiction; everything else cross-checks — 386 spans consistent across copy/chip/alt/screenshot, session ID matches, all six anchors resolve. Alt text claims "406 memories" the rendered image can't verify.

**Casey (mobile):** quickstart requires a one-thumb horizontal drag to read a truncated URL, no copy button; downloads ~1.1MB to see proof she can't read; footer targets 23px; orphaned arrows. What works: fast hero, lazy loading, big hamburger, persistent theme, zero page overflow.

## Minor Observations

- Favicon stroke `#22c55e` (green-500) off-token vs `#15803d`/`#4ade80`.
- No Open Graph / Twitter meta — link shares render unstyled for exactly the audience that shares links.
- Both screenshots dark-theme only; light-mode visitors never see the product in their theme.
- Floor contrasts pass AA exactly (4.55:1 at 12.8px) — zero headroom.
- Brand link and "Introduction" both point home — two affordances, one active state.
- The refusal log quietly demonstrates the site's own a11y rule enforcement — nice easter egg.

## Questions to Consider

1. Should mobile get a different proof hierarchy (refusal log promoted, screenshot demoted) instead of a shrunken desktop copy?
2. "Agent = Model + Harness" is the one line to remember — why is it the smallest element above the fold?
3. The page ends on a limitation. Principled candor or anticlimax — can it end on candor *and* the clone command without the SaaS closing-CTA cliché?
