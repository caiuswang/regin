<script setup>
import { ref } from 'vue'
import SiteIcon from '../components/SiteIcon.vue'
import Button from '../components/ui/Button.vue'
import HomeProof from '../components/HomeProof.vue'
import HomePillars from '../components/HomePillars.vue'

const CLONE_CMD = 'git clone https://github.com/caiuswang/regin\ncd regin && ./scripts/setup.sh'
const copied = ref(false)

async function copyQuickstart() {
  try {
    await navigator.clipboard.writeText(CLONE_CMD)
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  } catch { /* clipboard unavailable (http or permission) — the text stays selectable */ }
}
</script>

<template>
  <div class="container">
    <section class="hero">
      <h1>Agent = Model + Harness</h1>
      <p class="lead">
        Everyone tunes the model. regin is the harness — the layer around your
        coding agent that steers it before it acts, catches it as it acts, and
        traces which of those bets paid off.
      </p>
      <div class="hero-ctas">
        <a href="https://github.com/caiuswang/regin" class="btn btn-primary focus-visible:ring">
          <SiteIcon name="github" :size="17" />
          Clone on GitHub
        </a>
        <RouterLink to="/getting-started" class="btn btn-ghost focus-visible:ring">Read the guide</RouterLink>
      </div>
      <div class="quickstart-wrap">
        <pre class="quickstart"><code>{{ CLONE_CMD }}</code></pre>
        <Button variant="icon" class="quickstart-copy" :aria-label="copied ? 'Copied' : 'Copy quick-start commands'" @click="copyQuickstart">
          <SiteIcon :name="copied ? 'check' : 'copy'" :size="15" />
        </Button>
        <span class="sr-only" aria-live="polite">{{ copied ? 'Copied to clipboard' : '' }}</span>
      </div>
      <p class="quickstart-caption">Runs locally — Python + Node, one setup script. Early beta; pin a commit if you need stability.</p>
    </section>

    <section class="section" aria-labelledby="mechanisms-heading">
      <div class="section-head">
        <h2 id="mechanisms-heading">Two complementary mechanisms</h2>
        <p>A harness is what turns a non-deterministic model into a teammate you can trust on real work. It takes both halves:</p>
      </div>
      <div class="grid-2">
        <div class="card">
          <h3>Guides — steer it before it acts</h3>
          <p>The feedforward half: skills and docs that carry your conventions as local pattern guides, promoted to versioned skill bundles surfaced exactly when their triggers match.</p>
        </div>
        <div class="card">
          <h3>Sensors — catch it as it acts</h3>
          <p>The feedback half: hooks that watch every edit and force corrections when the agent drifts. A rule isn't prose the agent is asked to remember — it's a hook that refuses the edit and says why, in the same turn.</p>
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

    <section class="section closing-cta" aria-labelledby="cta-heading">
      <h2 id="cta-heading">Still here? Then it's probably time to run it.</h2>
      <div class="hero-ctas">
        <a href="https://github.com/caiuswang/regin" class="btn btn-primary focus-visible:ring">
          <SiteIcon name="github" :size="17" />
          Clone on GitHub
        </a>
        <RouterLink to="/getting-started" class="btn btn-ghost focus-visible:ring">Read the guide first</RouterLink>
      </div>
    </section>
  </div>
</template>
