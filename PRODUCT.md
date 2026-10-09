# Product

## Register

brand

## Platform

web

## Users

Developers who run AI coding agents (Claude Code; Kimi Code for task runs) on real codebases. Individuals first: the power-user who works with an agent daily and is tired of watching it repeat last week's mistakes. Team leads and platform engineers are the second audience and the growth path — people standardizing how a whole team's agents behave across a shared repo. The primary surface this document governs is the official website (`website/`), which speaks to people *evaluating* regin; the regin dashboard (`frontend/`) is a product-register surface, treated as a per-task override.

## Product Purpose

regin is a workbench for building software with AI coding agents. It runs one loop — **Spec → Run → Watch → Count** — across four layers, in this order: the **Workbench** (a feature row carries the spec, its acceptance tests and the tasks cut from it; defects are declared against the done-claim), **Runs** (task runs launched on Claude or Kimi, steered and stopped from `/live`, reporting back to the inbox), **Observability** (session traces, time and cost analytics, grades), and **Guidance** (patterns and skills, hook-level rule engines, cross-session memory, topic wikis). The website exists to make a skeptical developer understand that loop and clone the repo. Success: a visitor can name the four steps of the loop in under a minute, believes it runs on real software, and runs the quick start.

## Positioning

A workbench for building software with AI coding agents: plan the feature, run the agents, see what they did, count what they got wrong. Most tooling stops at running the agent; regin closes the loop by holding every feature to a countable outcome — rounds-to-done — and showing the run that produced it. Guidance (patterns, rules, memory) is the supporting layer that keeps the agent on-spec, not the headline.

## Conversion & proof

- Primary CTA: clone & install — https://github.com/caiuswang/regin (quick start: `./scripts/setup.sh`).
- Secondary CTA: read the Getting Started page for visitors not ready to install.
- The line a visitor remembers after 10 seconds: **Plan the feature, run the agents, see what they did, count what they got wrong.**
- Belief ladder: (1) my agents do real work, but I lose track of what was asked, what ran, and what came back wrong — the problem is real and mine; (2) the fix is a loop with a ledger, not a better prompt; (3) regin runs that loop end to end — spec and tasks, launched and steerable runs, traces, and a defect count per feature — with guidance underneath to keep the agent on-spec; (4) it's real running software I can install locally today; (5) early beta is fine because I can pin a commit.
- Proof on hand: real product screenshots — the Pipeline ledger, `/live`, the trace viewer, the memory browser and topics UI. No mockups, no invented testimonials or numbers. (No social proof yet; do not fabricate any.)

## Brand Personality

Warm and approachable — a knowledgeable colleague explaining a sharp idea, not a vendor pitching one. Plain language before jargon, diagrams before adjectives, honesty about maturity (the early-beta banner is a trust feature, not fine print). The warmth lives in voice, pacing, and explanatory generosity — never in hype.

## Anti-references

- Generic SaaS landing: gradient hero, floating dashboard mockups, logo marquee, pricing-table energy.
- AI-hype maximalism: glowing gradients, particle backgrounds, "revolutionary agentic AI" copy, sparkle iconography.
- Cold dev-tool austerity: terminal-dark density with no warmth. (The site's first iteration leaned this way; the brand deliberately moves away from it.)

## Design Principles

1. **Show the working tool.** Real screenshots of the real UI beat abstractions and mockups; the product is the proof.
2. **Explain the mechanism, don't hype the outcome.** The reader is a skeptical developer — earn belief with how it works (the ledger, the runs, the traces, the hooks), not with claims about it.
3. **Warmth through clarity, not decoration.** Approachability comes from plain language, generous explanation, and legible pacing — not from ornament or trend styling.
4. **Honest about maturity.** Early-beta status is stated plainly wherever expectations are set.
5. **One mental model everywhere.** Every page reinforces the loop Spec → Run → Watch → Count, and presents the layers in one order — Workbench, Runs, Observability, Guidance; nothing on the site should compete with that frame.

## Accessibility & Inclusion

WCAG 2.2 AA as a hard gate: ≥4.5:1 body-text contrast in both light and dark themes (verified by measurement, not eye), ≥3:1 non-text/focus contrast, ≥24px targets, full keyboard operability with visible focus, no color-only meaning, `prefers-reduced-motion` honored on every animation.
