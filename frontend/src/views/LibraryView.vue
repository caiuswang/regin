<script setup>
// The library's one workbench. A pattern is the source a skill is deployed
// from, so two rail entries described one thing twice — one entry, one header,
// and a tab strip that says the two belong together.
//
// The child URLs are unchanged (`/patterns`, `/skills`): the routes are
// absolute children of this shell, so every existing link, bookmark and
// CLI-printed URL still lands where it always did. A detail page stays a
// sibling of the shell rather than a child: it is a document, not a third
// reading of the list, and it already carries its own identity header.
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

const TABS = [
  { path: '/patterns', key: 'patterns', label: 'Patterns',
    blurb: 'Curated procedure guides synced from sibling source repos.' },
  { path: '/skills', key: 'skills', label: 'Skills',
    blurb: 'Skill packs deployed globally and per-project.' },
]

function isActive(tab) {
  return route.path === tab.path
}

const active = computed(() => TABS.find(isActive) || null)
const subtitle = computed(() => active.value?.blurb || '')
</script>

<template>
  <div class="space-y-4">
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Library</div>
        <h1 class="page-title">Patterns &amp; Skills</h1>
        <p class="page-subtitle">{{ subtitle }}</p>
      </div>
      <!-- The tabs are siblings of this header, not descendants, so a tab's
           own controls cannot be passed down as slot content. They teleport
           in here instead, which keeps every page's actions in the one place
           the eye already looks for them. -->
      <div id="library-actions" class="page-actions" />
    </header>

    <nav class="segmented mb-4" aria-label="Library sections" data-testid="library-tabs">
      <router-link
        v-for="tab in TABS"
        :key="tab.path"
        :to="tab.path"
        class="segmented-item focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ 'is-active': isActive(tab) }"
        :aria-current="isActive(tab) ? 'page' : undefined"
        :data-testid="`library-tab-${tab.key}`"
      >
        {{ tab.label }}
      </router-link>
    </nav>

    <!-- Deliberately not kept alive: a deactivated view's Teleport content
         stays in the target, so a cached tab would leave its buttons in the
         header of the tab you switched to. -->
    <router-view />
  </div>
</template>

<style scoped>
.page-header { align-items: flex-start; }
.page-subtitle { max-width: 56rem; }
</style>
