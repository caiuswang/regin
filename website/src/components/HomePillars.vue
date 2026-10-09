<script setup>
import SiteIcon from './SiteIcon.vue'
import { PILLARS, SECONDARY } from '../content/features.js'
</script>

<template>
  <section class="section" aria-labelledby="pillars-heading">
    <div class="section-head">
      <p class="eyebrow">How it works</p>
      <h2 id="pillars-heading">Each layer, with real output</h2>
      <p>Nothing below is a mockup — every artifact is regin running on its own repo, with the feature or trace it came from named.</p>
    </div>

    <div v-for="pillar in PILLARS" :key="pillar.title" class="pillar">
      <div class="pillar-body">
        <p class="eyebrow">{{ pillar.layer }}</p>
        <h3 class="pillar-title">
          <span class="pillar-glyph"><SiteIcon :name="pillar.icon" :size="18" /></span>
          {{ pillar.title }}
        </h3>
        <p>{{ pillar.body }}</p>
        <RouterLink :to="pillar.link.to" class="more">
          {{ pillar.link.label }}
          <SiteIcon name="arrow-right" :size="14" />
        </RouterLink>
      </div>
      <div class="pillar-artifact">
        <template v-if="pillar.artifact === 'code'">
          <pre class="artifact-log"><code>{{ pillar.code }}</code></pre>
          <p class="artifact-caption">{{ pillar.codeCaption }}</p>
        </template>
        <template v-else>
          <ul class="artifact-stats">
            <li v-for="s in pillar.stats" :key="s"><code>{{ s }}</code></li>
          </ul>
          <p class="artifact-caption">{{ pillar.statsCaption }}</p>
        </template>
      </div>
    </div>

    <ul class="mini-list">
      <li v-for="item in SECONDARY" :key="item.title">
        <RouterLink :to="item.to" class="mini-row focus-visible:ring">
          <span class="mini-title">{{ item.title }} <span v-if="item.experimental" class="badge-exp">experimental</span></span>
          <span class="mini-body">{{ item.body }}</span>
          <SiteIcon name="arrow-right" :size="16" />
        </RouterLink>
      </li>
    </ul>
  </section>
</template>
