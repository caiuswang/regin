<script setup>
import QuickStart from './QuickStart.vue'
import overviewShot from '../../assets/shots/pixel/overview.webp'
import { LAYERS } from '../../content/features.js'

const HUD_STATS = [
  ['FEATURES ', ['109', 'ink']],
  ['TASKS ', ['378', 'ink']],
  ['DEFECTS ', ['139', 'ink']],
  ['SHIPPED ', ['30%', 'green']],
]
</script>

<template>
  <section id="top" aria-labelledby="hero-h" data-screen-label="Hero" class="land-panel hero-panel">
    <div class="hud-bar land-hud">
      <span class="land-hud-title">WORLD 1 · <span class="hud-accent">REGIN BUILDS REGIN</span></span>
      <span class="land-hud-stats">
        <span v-for="([k, [v, tone]]) in HUD_STATS" :key="k" class="land-hud-stat">{{ k }}<b :class="`tone-${tone}`">{{ v }}</b></span>
      </span>
    </div>
    <div class="hero-body">
      <div class="hero-text">
        <p class="land-kicker">▶ AI AGENT WORKBENCH</p>
        <h1 id="hero-h" class="hero-h1">Plan the feature, run the agents, see what they did, count what they got wrong.</h1>
        <p class="land-lead hero-lead">regin runs one loop around the agent you already use. Every number on this page is real output from regin building itself.</p>
        <div class="land-ctas">
          <a href="https://github.com/caiuswang/regin" class="key-primary focus-visible:ring">▶ CLONE ON GITHUB</a>
          <RouterLink to="#play" class="land-text-link focus-visible:ring">Play the loop ↓</RouterLink>
        </div>
        <QuickStart />
      </div>
      <figure class="land-shot hero-shot">
        <div class="land-shot-frame hero-shot-crop">
          <img :src="overviewShot" alt="The regin dashboard's Pipeline Overview in the Pixel theme: 109 features, 378 tasks and 139 defects, 30% shipped." width="2880" height="1800" />
        </div>
        <figcaption class="land-figcaption">PIPELINE OVERVIEW · THE REPO THAT BUILDS REGIN, LIVE</figcaption>
      </figure>
    </div>
    <slot name="scene" />
    <nav aria-label="The regin loop" class="loop-tiles">
      <RouterLink v-for="(layer, i) in LAYERS" :key="layer.anchor" :to="`#${layer.anchor}`" class="loop-tile focus-visible:ring">
        <span class="loop-tile-head">
          <span class="tone-green">{{ layer.code }} {{ layer.verb }}</span>
          <span class="tone-dim">{{ i === LAYERS.length - 1 ? '↺' : '→' }}</span>
        </span>
        <span class="loop-tile-name">{{ layer.name }}</span>
        <span class="loop-tile-summary">{{ layer.summary }}</span>
      </RouterLink>
    </nav>
  </section>
</template>
