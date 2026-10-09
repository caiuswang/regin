<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import SiteHeader from './components/SiteHeader.vue'
import SiteFooter from './components/SiteFooter.vue'
import DocsShell from './components/docs/DocsShell.vue'

const route = useRoute()
const isHome = computed(() => route.name === 'home')
</script>

<template>
  <a href="#main" class="skip-link focus-visible:ring">Skip to content</a>
  <SiteHeader :variant="isHome ? 'home' : 'docs'" />
  <main id="main">
    <RouterView v-if="isHome" />
    <DocsShell v-else :page="route.meta.page ?? null" :path="route.path">
      <RouterView />
    </DocsShell>
  </main>
  <SiteFooter v-if="isHome" />
</template>
