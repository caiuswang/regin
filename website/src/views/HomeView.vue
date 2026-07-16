<script setup>
import SiteIcon from '../components/SiteIcon.vue'
import { FEATURES } from '../content/features.js'
</script>

<template>
  <div class="container">
    <section class="hero">
      <p class="eyebrow">Agent = Model + Harness</p>
      <h1>The <span class="accent">harness layer</span> for AI coding agents</h1>
      <p class="lead">
        regin is not an agent platform — it's the layer <em>around</em> the agent:
        pattern guides that steer it before it acts, enforced rules that correct it
        as it acts, and the observability to know whether either is working.
      </p>
      <div class="hero-ctas">
        <RouterLink to="/getting-started" class="btn btn-primary focus-visible:ring">
          Get started
          <SiteIcon name="arrow-right" :size="16" />
        </RouterLink>
        <RouterLink to="/architecture" class="btn btn-ghost focus-visible:ring">How it works</RouterLink>
      </div>
    </section>

    <section class="section" aria-labelledby="mechanisms-heading">
      <div class="section-head">
        <h2 id="mechanisms-heading">Two complementary mechanisms</h2>
        <p>A harness turns a non-deterministic model into a teammate you can trust on real work.</p>
      </div>
      <div class="grid-2">
        <div class="card duo-card">
          <h3>Guides — feedforward</h3>
          <p>Skills and docs that steer the agent <strong>before</strong> it acts: local pattern guides for your conventions, promoted to versioned skill bundles surfaced exactly when their triggers match.</p>
        </div>
        <div class="card duo-card">
          <h3>Sensors — feedback</h3>
          <p>Hooks that observe the agent <strong>as</strong> it acts and force corrections when it drifts: a rule isn't prose the agent is asked to remember — it's a hook that refuses the edit and says why, in the same turn.</p>
        </div>
      </div>
    </section>

    <section class="section" aria-labelledby="features-heading">
      <div class="section-head">
        <h2 id="features-heading">What regin provides</h2>
        <p>Everything rides the same trace: patterns, skills, and rules are guesses about what the agent needs — trace is how you find out which guesses were right.</p>
      </div>
      <div class="grid-3">
        <div v-for="f in FEATURES" :key="f.title" class="card feature-card">
          <span class="icon-wrap"><SiteIcon :name="f.icon" :size="22" /></span>
          <h3>{{ f.title }} <span v-if="f.experimental" class="badge-exp">experimental</span></h3>
          <p>{{ f.body }}</p>
          <RouterLink :to="f.link.to" class="more">{{ f.link.label }} →</RouterLink>
        </div>
      </div>
    </section>

    <section class="section" aria-labelledby="scope-heading">
      <div class="grid-2">
        <div class="card">
          <h3 id="scope-heading">What regin is not</h3>
          <p>Not a chat UI, not an agent runtime, not a model. It assumes you already have an agent and a codebase, and makes that pairing more productive. The question it answers is <em>“how do I keep this agent on-spec across a real team and a real repo.”</em></p>
        </div>
        <div class="card">
          <h3>Supported agents</h3>
          <p><strong>Claude only, today.</strong> The rule layer needs hooks deep enough to intercept tool calls, edits, and prompts in flight — Claude is currently the only widely-available agent with a hook system that mature. Codex and Kimi exist as provider adapters, not yet wired through the rule layer.</p>
        </div>
      </div>
    </section>
  </div>
</template>
