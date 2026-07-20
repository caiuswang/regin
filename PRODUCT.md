# Product

## Register

brand

## Platform

web

## Users

Developers who run AI coding agents (Claude Code today) on real codebases. Individuals first: the power-user who works with an agent daily and is tired of watching it repeat last week's mistakes. Team leads and platform engineers are the second audience and the growth path — people standardizing how a whole team's agents behave across a shared repo. The primary surface this document governs is the official website (`website/`), which speaks to people *evaluating* regin; the regin dashboard (`frontend/`) is a product-register surface, treated as a per-task override.

## Product Purpose

regin is harness infrastructure for AI coding agents: pattern guides that steer the agent before it acts (feedforward), enforced hook-level rules that correct it as it acts (feedback), session tracing to see which of those bets paid off, and cross-session memory so one session's lesson becomes the next session's starting point. The website exists to make a skeptical developer understand the harness mental model and clone the repo. Success: a visitor understands "harness" in under a minute, believes the mechanism, and runs the quick start.

## Positioning

Agent = Model + Harness — regin is the harness half. Everyone tunes the model; regin is the layer around it that makes an agent trustworthy on a real team and a real repo.

## Conversion & proof

- Primary CTA: clone & install — https://github.com/caiuswang/regin (quick start: `./scripts/setup.sh`).
- Secondary CTA: read the Getting Started page for visitors not ready to install.
- The line a visitor remembers after 10 seconds: **Agent = Model + Harness.**
- Belief ladder: (1) my agent drifts and repeats mistakes — the problem is real and mine; (2) the fix is the harness layer, not a better model; (3) regin supplies both halves — advisory guides *and* enforced rules — plus the tracing to prove which work; (4) it's real running software I can install locally today; (5) early beta is fine because I can pin a commit.
- Proof on hand: real product screenshots — actual trace viewer, memory browser, and topics UI captures. No mockups, no invented testimonials. (No social proof yet; do not fabricate any.)

## Brand Personality

Warm and approachable — a knowledgeable colleague explaining a sharp idea, not a vendor pitching one. Plain language before jargon, diagrams before adjectives, honesty about maturity (the early-beta banner is a trust feature, not fine print). The warmth lives in voice, pacing, and explanatory generosity — never in hype.

## Anti-references

- Generic SaaS landing: gradient hero, floating dashboard mockups, logo marquee, pricing-table energy.
- AI-hype maximalism: glowing gradients, particle backgrounds, "revolutionary agentic AI" copy, sparkle iconography.
- Cold dev-tool austerity: terminal-dark density with no warmth. (The site's first iteration leaned this way; the brand deliberately moves away from it.)

## Design Principles

1. **Show the working tool.** Real screenshots of the real UI beat abstractions and mockups; the product is the proof.
2. **Explain the mechanism, don't hype the outcome.** The reader is a skeptical developer — earn belief with how it works (hooks, rules, traces), not with claims about it.
3. **Warmth through clarity, not decoration.** Approachability comes from plain language, generous explanation, and legible pacing — not from ornament or trend styling.
4. **Honest about maturity.** Early-beta status is stated plainly wherever expectations are set.
5. **One mental model everywhere.** Every page reinforces Agent = Model + Harness; nothing on the site should compete with that frame.

## Accessibility & Inclusion

WCAG 2.2 AA as a hard gate: ≥4.5:1 body-text contrast in both light and dark themes (verified by measurement, not eye), ≥3:1 non-text/focus contrast, ≥24px targets, full keyboard operability with visible focus, no color-only meaning, `prefers-reduced-motion` honored on every animation.
