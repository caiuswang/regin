<script setup>
import SiteIcon from '../components/SiteIcon.vue'
import HomeProof from '../components/HomeProof.vue'
import HomePillars from '../components/HomePillars.vue'
</script>

<template>
  <div class="container">
    <section class="hero">
      <p class="eyebrow">Agent = Model + Harness</p>
      <h1>The <span class="accent">harness layer</span> for AI coding agents</h1>
      <p class="lead">
        Everyone tunes the model. regin is the layer around it — guides that steer
        your agent before it acts, rules that catch it as it acts, and the tracing
        to know which of those bets paid off.
      </p>
      <div class="hero-ctas">
        <a href="https://github.com/caiuswang/regin" class="btn btn-primary focus-visible:ring">
          <SiteIcon name="github" :size="17" />
          Clone on GitHub
        </a>
        <RouterLink to="/getting-started" class="btn btn-ghost focus-visible:ring">Read the guide</RouterLink>
      </div>
      <pre class="quickstart"><code>git clone https://github.com/caiuswang/regin
cd regin &amp;&amp; ./scripts/setup.sh</code></pre>
      <p class="quickstart-caption">Runs locally — Python + Node, one setup script. Early beta; pin a commit if you need stability.</p>
    </section>

    <section class="section" aria-labelledby="mechanisms-heading">
      <div class="section-head">
        <h2 id="mechanisms-heading">Two complementary mechanisms</h2>
        <p>A harness is what turns a non-deterministic model into a teammate you can trust on real work. It takes both halves:</p>
      </div>
      <div class="grid-2">
        <div class="card duo-card">
          <h3>Guides — feedforward</h3>
          <p>Skills and docs that steer the agent <strong>before</strong> it acts: your conventions as local pattern guides, promoted to versioned skill bundles surfaced exactly when their triggers match.</p>
        </div>
        <div class="card duo-card">
          <h3>Sensors — feedback</h3>
          <p>Hooks that watch the agent <strong>as</strong> it acts and force corrections when it drifts: a rule isn't prose the agent is asked to remember — it's a hook that refuses the edit and says why, in the same turn.</p>
        </div>
      </div>
    </section>

    <HomeProof />
    <HomePillars />

    <section class="section closing-cols" aria-labelledby="scope-heading">
      <div>
        <h3 id="scope-heading">What regin is not</h3>
        <p>Not a chat UI, not an agent runtime, not a model. It assumes you already have an agent and a codebase, and makes that pairing more productive. The question it answers is <em>“how do I keep this agent on-spec across a real team and a real repo.”</em></p>
      </div>
      <div>
        <h3>Supported agents</h3>
        <p><strong>Claude only, today.</strong> The rule layer needs hooks deep enough to intercept tool calls, edits, and prompts in flight — Claude is currently the only widely-available agent with a hook system that mature. Codex and Kimi exist as provider adapters, not yet wired through the rule layer.</p>
      </div>
    </section>
  </div>
</template>
