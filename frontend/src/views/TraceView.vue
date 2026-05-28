<script setup>
import { useRoute } from 'vue-router'

const route = useRoute()

const tabs = [
  { path: '/trace/sessions', label: 'Sessions' },
  { path: '/trace/triggers', label: 'Rule Triggers' },
  { path: '/trace/skill-reads', label: 'Skill Reads' },
  { path: '/trace/mcp-calls', label: 'MCP Calls' },
  { path: '/trace/ingest-errors', label: 'Ingest Errors' },
]

function isActive(path) {
  return route.path.startsWith(path)
}
</script>

<template>
  <div>
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Observability</div>
        <h1 class="page-title">Trace</h1>
        <p class="page-subtitle">Hook-captured spans across sessions, rule triggers, skill reads, and MCP calls.</p>
      </div>
    </header>

    <div class="segmented mb-6">
      <router-link
        v-for="tab in tabs"
        :key="tab.path"
        :to="tab.path"
        class="segmented-item focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ 'is-active': isActive(tab.path) }"
      >
        {{ tab.label }}
      </router-link>
    </div>

    <router-view />
  </div>
</template>
