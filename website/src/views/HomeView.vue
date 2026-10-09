<script setup>
import { ref } from 'vue'
import SiteIcon from '../components/SiteIcon.vue'
import Button from '../components/ui/Button.vue'
import HomeProof from '../components/HomeProof.vue'
import HomePillars from '../components/HomePillars.vue'
import BlockShot from '../components/blocks/BlockShot.vue'
import HeroScene from '../components/HeroScene.vue'
import PlayTheLoop from '../components/PlayTheLoop.vue'
import overviewShot from '../assets/shots/pixel/overview.webp'
import { LAYERS, LOOP } from '../content/features.js'


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
      <div class="hero-badge">
        <span class="dot" aria-hidden="true"></span>
        Early beta · <strong>Runs locally</strong>
      </div>
      <h1>Plan the feature, run the agents, see what they did, count what they got wrong.</h1>
      <p class="lead">
        regin is a workbench for building software with AI coding agents. It runs
        one loop around the agent you already use:
      </p>
      <ol class="hero-loop" aria-label="The regin loop">
        <li v-for="step in LOOP" :key="step">{{ step }}</li>
      </ol>
      <div class="hero-ctas">
        <a href="https://github.com/caiuswang/regin" class="btn btn-primary focus-visible:ring">
          <SiteIcon name="github" :size="17" />
          Clone on GitHub
        </a>
        <RouterLink to="/getting-started" class="btn btn-ghost focus-visible:ring">Read the guide</RouterLink>
      </div>
      <div class="terminal">
        <div class="terminal-bar" aria-hidden="true">
          <span class="tdot"></span><span class="tdot"></span><span class="tdot"></span>
          <span class="tlabel">bash</span>
        </div>
        <div class="quickstart-wrap">
          <pre class="quickstart"><code>{{ CLONE_CMD }}</code></pre>
          <Button variant="icon" class="quickstart-copy" :aria-label="copied ? 'Copied' : 'Copy quick-start commands'" @click="copyQuickstart">
            <SiteIcon :name="copied ? 'check' : 'copy'" :size="15" />
          </Button>
          <span class="sr-only" aria-live="polite">{{ copied ? 'Copied to clipboard' : '' }}</span>
        </div>
      </div>
      <p class="quickstart-caption">Runs locally — Python + Node, one setup script, then <RouterLink to="/getting-started#activate-hooks">install the hooks</RouterLink> to wire it into your agent. Early beta; pin a commit if you need stability.</p>
      <HeroScene />
    </section>

    <PlayTheLoop />

    <section class="section" aria-labelledby="layers-heading">
      <div class="section-head">
        <p class="eyebrow">The workbench</p>
        <h2 id="layers-heading">Four layers, in the order you meet them</h2>
        <p>The first two are where you spend the day; the last two make the runs inspectable and keep the agent on-spec.</p>
      </div>
      <div class="grid-2">
        <RouterLink v-for="layer in LAYERS" :key="layer.name" :to="layer.to" class="card layer-card focus-visible:ring">
          <h3>
            <SiteIcon :name="layer.icon" :size="18" />
            {{ layer.name }}
          </h3>
          <p>{{ layer.summary }}</p>
        </RouterLink>
      </div>
    </section>

    <section class="section" aria-labelledby="overview-heading">
      <div class="section-head">
        <p class="eyebrow">The workbench, in use</p>
        <h2 id="overview-heading">This is regin building regin</h2>
        <p>The Pipeline Overview of the project that builds regin, scoped to that one repo: features in the workshop, tasks on the board, defects in the bug den, and the one decision waiting on a human. The first feature in flight is the one that rewrote this page.</p>
      </div>
      <BlockShot :src="overviewShot" alt="The regin dashboard's Pipeline Overview in the Pixel theme, scoped to the regin project: 109 features, 378 tasks and 139 defects in the loop scene, the project panel at 30% shipped, the features in flight and one defect needing a decision." />
    </section>

    <HomePillars />
    <HomeProof />

    <section class="section closing-cols" aria-labelledby="scope-heading">
      <div>
        <h3 id="scope-heading">What regin is not</h3>
        <p>Not a chat UI, not a model, not a hosted service. It assumes you already have a coding agent and a codebase, and runs locally beside them. The question it answers is <em>“what did the agents build, what did it cost, and what did they get wrong.”</em></p>
      </div>
      <div>
        <h3>Supported agents</h3>
        <p><strong>Claude Code — full:</strong> hooks, rule engines, trace, memory, and task runs through the Agent SDK. <strong>Kimi Code — task runs only</strong> (ACP or tmux), <span class="badge-exp">experimental</span>: runs are unattended and a steer is cancel-then-re-prompt. Codex and generic adapters exist but cannot be armed for task runs.</p>
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
