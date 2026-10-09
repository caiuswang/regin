<script setup>
import HeroSection from '../components/landing/HeroSection.vue'
import LevelSection from '../components/landing/LevelSection.vue'
import ContinueSection from '../components/landing/ContinueSection.vue'
import HeroScene from '../components/HeroScene.vue'
import PlayTheLoop from '../components/PlayTheLoop.vue'
import { LAYERS, PILLARS } from '../content/features.js'
</script>

<template>
  <div class="landing">
    <HeroSection>
      <template #scene><HeroScene /></template>
    </HeroSection>
    <PlayTheLoop />
    <LevelSection
      v-for="(level, i) in PILLARS" :key="level.layer"
      :n="i + 1" :anchor="LAYERS[i].anchor" :level="level"
    />
    <ContinueSection />
  </div>
</template>
