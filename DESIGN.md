---
name: regin
description: Official website for regin — the harness layer for AI coding agents
colors:
  harness-green: "#15803d"
  harness-green-dark-mode: "#4ade80"
  green-tint: "#dcfce7"
  green-tint-ink: "#14532d"
  ink: "#0f172a"
  ink-muted: "#475569"
  ink-faint: "#64748b"
  fog: "#f8fafc"
  surface: "#ffffff"
  surface-recessed: "#f1f5f9"
  hairline: "#e2e8f0"
  hairline-strong: "#cbd5e1"
  amber-tint: "#fef3c7"
  amber-ink: "#78350f"
  night: "#0b1120"
  night-surface: "#111a2e"
  night-ink: "#f1f5f9"
  night-ink-muted: "#a5b4cb"
typography:
  display:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(2.3rem, 6vw, 3.6rem)"
    fontWeight: 800
    lineHeight: 1.08
    letterSpacing: "-0.035em"
  headline:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "2.2rem"
    fontWeight: 800
    lineHeight: 1.15
    letterSpacing: "-0.03em"
  title:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.45rem"
    fontWeight: 700
    lineHeight: 1.25
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.65
  label:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 700
    lineHeight: 1.4
    letterSpacing: "0.12em"
  mono:
    fontFamily: "JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "0.85rem"
    fontWeight: 400
    lineHeight: 1.7
rounded:
  xs: "6px"
  sm: "8px"
  md: "10px"
  lg: "12px"
  xl: "14px"
  pill: "999px"
spacing:
  xs: "0.5rem"
  sm: "0.75rem"
  md: "1.25rem"
  lg: "1.5rem"
  xl: "2.5rem"
  section: "3.5rem"
  hero: "5rem"
components:
  button-primary:
    backgroundColor: "{colors.harness-green}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "0.7rem 1.4rem"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "0.7rem 1.4rem"
  button-icon:
    backgroundColor: "transparent"
    textColor: "{colors.ink-muted}"
    rounded: "{rounded.sm}"
    size: "2.4rem"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.xl}"
    padding: "1.5rem"
  nav-link:
    textColor: "{colors.ink-muted}"
    rounded: "{rounded.sm}"
    padding: "0.45rem 0.8rem"
  nav-link-active:
    backgroundColor: "{colors.green-tint}"
    textColor: "{colors.harness-green}"
    rounded: "{rounded.sm}"
    padding: "0.45rem 0.8rem"
---

# Design System: regin

## 1. Overview

**Creative North Star: "The Workshop Manual"**

A well-thumbed engineer's handbook: structured, legible, and generous with explanation. The warmth in this system comes from craft and clarity — plain language, patient pacing, real screenshots of the working tool — never from ornament, gradients, or trend styling. Pages read like a knowledgeable colleague walking you through a sharp idea, one section at a time, with the diagram already drawn.

The system explicitly rejects the three anti-references in PRODUCT.md: the generic SaaS landing (gradient hero, floating mockups, logo marquee), AI-hype maximalism (glow, particles, sparkle), and cold dev-tool austerity (terminal-dark density with no warmth). It is light-first with a full dark theme, flat with hairline borders, and spends its single accent — Harness Green — only where the mechanism speaks.

**Key Characteristics:**
- One typeface (Inter) in many weights; JetBrains Mono for everything the machine says
- One accent color with a strict budget; slate neutrals do all other work
- Flat surfaces separated by 1px hairlines; shadow only on things that float
- Measured accessibility: every text/background pair verified ≥4.5:1 in both themes
- Dark mode is a first-class palette, not an inversion

## 2. Colors

Slate neutrals carry the interface; one deep green carries the meaning.

### Primary
- **Harness Green** (#15803d light / #4ade80 dark): the color of the mechanism working — a passing check, a live hook. Used for links, the active nav state, the one primary CTA per view, and the focus ring. Chosen at green-700 in light mode because green-600 measured 3.1–3.3:1 against light surfaces; this shade measures 4.8–5.0:1.
- **Green Tint** (#dcfce7, ink #14532d): quiet green wash for the active nav pill, info callouts, and icon chips; its dark-mode counterparts are #14321f / #86efac.

### Neutral
- **Ink** (#0f172a): all primary text on light surfaces; also the background of code blocks in both themes.
- **Muted Ink** (#475569): body prose, table cells, nav resting state (7.2:1 on Fog).
- **Faint Ink** (#64748b): footer text and tertiary metadata only — the floor of legibility (4.55:1); nothing smaller or lighter.
- **Fog** (#f8fafc): the page background. A cool near-white — deliberately not cream.
- **Surface** (#ffffff) and **Recessed Surface** (#f1f5f9): cards and table headers respectively.
- **Hairline** (#e2e8f0) / **Strong Hairline** (#cbd5e1): borders; strong is reserved for interactive edges (ghost buttons).
- **Night set** (#0b1120 page, #111a2e surface, #f1f5f9 ink, #a5b4cb muted): the dark theme, re-derived — softened toward GitHub/VSCode darks, never a pure inversion, never pure black.

### Semantic
- **Amber Tint** (#fef3c7, ink #78350f): warnings and security callouts only (dark: #372a12 / #fcd34d). Never decorative.

### Named Rules
**The Green Budget Rule.** Harness Green appears only where the mechanism speaks: links, active nav, one primary CTA per view, focus rings. If green exceeds ~10% of a screen, something is misusing it.
**The Measured Pair Rule.** No color pair ships on trust. Every foreground/background combination is computed against WCAG AA (≥4.5:1 text, ≥3:1 non-text) in both themes before it lands.

## 3. Typography

**Display/Body Font:** Inter (with ui-sans-serif, system-ui fallback), loaded non-blocking
**Mono Font:** JetBrains Mono (with ui-monospace, Menlo fallback)

**Character:** One family, many weights — the handbook voice. Inter at 800 with tight tracking does the talking at the top of a page; the same face at 400/1.65 explains below. Mono is reserved for what the machine says: commands, settings keys, file paths.

### Hierarchy
- **Display** (800, clamp(2.3rem, 6vw, 3.6rem), 1.08, -0.035em): the hero headline only — one per site, not per page.
- **Headline** (800, 2.2rem, 1.15, -0.03em): doc page titles (`.page-title`).
- **Title** (700, 1.45rem, 1.25, -0.02em): section headings (`h2`), separated by a hairline rule above.
- **Body** (400, 16px, 1.65): prose, capped at a 72ch measure.
- **Label** (700, 0.75rem, 0.12em tracked uppercase): the eyebrow and TOC heading — used once per page at most, never as scaffolding above every section.
- **Mono** (400, 0.85rem, 1.7): code blocks (light text on Ink in both themes) and inline code chips.

### Named Rules
**The 72ch Rule.** Article prose never exceeds a 72-character measure, whatever the viewport.
**The Machine Voice Rule.** Anything the user would type or the system would emit — commands, keys, paths, values — is set in mono, always. Prose never wears mono for emphasis.

## 4. Elevation

Flat, borders-first. Depth is conveyed by 1px hairline borders and the two-step surface ladder (Fog page → white Surface → Recessed Surface), not by shadows. Exactly two things cast shadows, because they genuinely float above the page: the sticky header (which also blurs the content scrolling beneath it) and the opened mobile menu.

### Shadow Vocabulary
- **Float** (`box-shadow: 0 1px 3px rgba(15,23,42,0.08), 0 8px 24px rgba(15,23,42,0.06)`; dark: same geometry at black 0.4/0.35): only on the sticky header and the mobile nav sheet.

### Named Rules
**The Float Rule.** If it doesn't physically overlay other content, it doesn't get a shadow. Cards are flat.

## 5. Components

Crisp and instructive — the sharp edges of a good diagram. Every element labels itself; hierarchy is always legible; hover states shift color, never layout.

### Buttons
- **Shape:** gently rounded (10px); icon buttons are square 2.4rem at 8px radius.
- **Primary:** white on Harness Green (0.7rem 1.4rem padding, weight 600); hover deepens the green ~15% toward ink.
- **Ghost:** ink text, Strong Hairline border, transparent; hover fills with Recessed Surface.
- **Hover / Focus:** 200ms color/background transitions only — no transforms, no layout shift; 2px Harness Green outline offset 2px on `:focus-visible`, everywhere.

### Cards / Containers
- **Corner Style:** 14px.
- **Background:** Surface on Fog; feature cards open with a 2.6rem green-tint icon chip.
- **Shadow Strategy:** none (see The Float Rule); 1px Hairline border does the work.
- **Internal Padding:** 1.5rem.
- **Callouts:** 12px radius, tinted fills (Green Tint for info, Amber Tint for warnings) with an inline SVG icon and matching deep-toned text; never a colored side-stripe.

### Navigation
- **Style:** sticky, blurred header (4rem tall) with hairline underline; brand mark + "beta" pill left, links right.
- **Links:** Muted Ink at 0.925rem/500; hover lifts to Ink on Recessed Surface; the active page holds a Green Tint pill (`aria-current="page"`).
- **Mobile:** below 768px the nav collapses behind a labeled icon button (`aria-expanded`) into a full-width sheet with the Float shadow; links close it on tap.

### Code Blocks (signature component)
The site's proof-of-work surface. Ink-dark panels (12px radius, 1px border) in both themes, JetBrains Mono at 0.85rem/1.7, light slate text, horizontal scroll contained inside the block — the page itself never scrolls sideways. Inline code sits in a Recessed chip (6px radius) at 0.85em.

### Tables
Wrapped in a bordered, rounded (12px) scroll container; Recessed Surface header row; hairline row separators; keys and defaults in mono chips.

## 6. Do's and Don'ts

### Do:
- **Do** hold every new color pair to The Measured Pair Rule — compute the ratio (≥4.5:1 text, ≥3:1 non-text) in both themes before shipping.
- **Do** keep Harness Green scarce (The Green Budget Rule): links, active state, one CTA, focus ring — nothing else.
- **Do** use real product screenshots as imagery; the working tool is the proof (PRODUCT.md: "Show the working tool").
- **Do** contain overflow locally: code blocks and tables scroll inside their own containers; `scrollWidth ≤ clientWidth` holds on the page at every viewport.
- **Do** honor `prefers-reduced-motion` with a near-instant alternative for every transition.
- **Do** keep icon-only buttons labeled (`aria-label`) and all icons as inline SVG.

### Don't:
- **Don't** build the "generic SaaS landing" (PRODUCT.md anti-reference): no gradient hero, no floating dashboard mockups, no logo marquee, no pricing-table energy.
- **Don't** ship "AI-hype maximalism": no glowing gradients, no particle backgrounds, no sparkle iconography, no "revolutionary agentic AI" copy.
- **Don't** drift back into "cold dev-tool austerity": light-first, generous line-height (1.65), patient explanatory copy — the dark theme is an option, not the identity.
- **Don't** use emoji as icons, gradient text, glassmorphism, colored side-stripe borders (`border-left > 1px`), or a tracked-uppercase eyebrow above every section — one Label per page, maximum.
- **Don't** put shadows on cards or transforms on hovers; if it looks lifted or it moves on hover, it's off-system.
- **Don't** set prose in mono or let any text pair fall below 4.5:1 — "light gray for elegance" is prohibited.
