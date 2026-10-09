<script setup>
import { useRoute } from 'vue-router'
import DocCard from '../components/docs/DocCard.vue'

const route = useRoute()
</script>

<template>
  <DocCard level="GAME OVER" hud="404" title="Page not found" :lead="`Nothing lives at ${route.path}. The world map lists every page.`">
    <RouterLink to="/" class="key-primary">▶ CONTINUE</RouterLink>
  </DocCard>
</template>
