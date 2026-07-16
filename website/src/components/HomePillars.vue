<script setup>
import SiteIcon from './SiteIcon.vue'
import memoryShot from '../assets/shots/memory-dark.png'
import memoryShotMobile from '../assets/shots/memory-dark-mobile.png'
import memoryShotWebp from '../assets/shots/memory-dark.webp'
import memoryShotMobileWebp from '../assets/shots/memory-dark-mobile.webp'
import { PILLARS, SECONDARY } from '../content/features.js'
</script>

<template>
  <section class="section" aria-labelledby="pillars-heading">
    <div class="section-head">
      <h2 id="pillars-heading">The three layers that do the work</h2>
      <p>Guides steer; these three are how the harness senses, corrects, and remembers — rules enforce the spec, the trace shows what happened, memory keeps what it taught.</p>
    </div>

    <div v-for="pillar in PILLARS" :key="pillar.title" class="pillar">
      <div class="pillar-body">
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
        <template v-else-if="pillar.artifact === 'stats'">
          <ul class="artifact-stats">
            <li v-for="s in pillar.stats" :key="s"><code>{{ s }}</code></li>
          </ul>
          <p class="artifact-caption">{{ pillar.statsCaption }}</p>
        </template>
        <template v-else>
          <figure class="shot-frame shot-frame-sm">
            <picture>
              <source media="(max-width: 767px)" type="image/webp" :srcset="memoryShotMobileWebp" width="1280" height="1280" />
              <source media="(max-width: 767px)" :srcset="memoryShotMobile" width="1280" height="1280" />
              <source type="image/webp" :srcset="memoryShotWebp" width="2016" height="1232" />
              <img :src="memoryShot" width="2016" height="1232" loading="lazy" :alt="pillar.shotAlt" />
            </picture>
            <figcaption>{{ pillar.shotCaption }}</figcaption>
          </figure>
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
