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

    <!-- Key the child by path so navigating between two same-route URLs
         (e.g. session A → session B, or a "view run" / "launched from
         session" jump) remounts the view and reloads its data — otherwise
         Vue reuses the instance, onMounted never re-fires, and the URL
         changes while the content stays stale. Query-only changes (the
         ?span= deep-link) keep the same path, so they still update in
         place via SessionTraceView's own query watcher. -->
    <router-view v-slot="{ Component }">
      <component :is="Component" :key="$route.path" />
    </router-view>
  </div>
</template>
