<script setup>
import { useRoute } from 'vue-router'
import Button from '../components/ui/Button.vue'
import Icon from '../components/ui/Icon.vue'
import { useTraceHeader } from '../composables/useTraceHeader'

const route = useRoute()
const header = useTraceHeader()

const tabs = [
  { path: '/trace/sessions', label: 'Sessions', countKey: 'tabCount' },
  { path: '/trace/triggers', label: 'Rule Triggers' },
  { path: '/trace/skill-reads', label: 'Skill Reads' },
  { path: '/trace/mcp-calls', label: 'MCP Calls' },
  { path: '/trace/ingest-errors', label: 'Ingest Errors' },
]

function isActive(path) {
  return route.path.startsWith(path)
}

// A tab's count only means anything while that tab is the one publishing it.
function tabCount(tab) {
  if (!tab.countKey || !isActive(tab.path)) return null
  const n = header.value?.[tab.countKey]
  return Number.isFinite(n) ? n : null
}
</script>

<template>
  <div>
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Observability · Trace</div>
        <h1 class="page-title">
          Trace
          <span v-if="header?.activeCount" class="live-pill">
            <span class="live-pill__dot" aria-hidden="true"></span>
            {{ header.activeCount }} active now
          </span>
        </h1>
        <p class="page-subtitle">Unified telemetry of skill reads, file edits, rule checks, and plan-mode entries per agent session.</p>
      </div>
      <div v-if="header?.onRefresh" class="page-actions">
        <Button
          class="quiet-btn"
          aria-label="Refresh the list"
          :disabled="header.refreshing"
          @click="header.onRefresh()"
        >
          <Icon name="refresh-cw" :size="14" :class="{ 'quiet-btn__spin': header.refreshing }" />
          {{ header.refreshing ? 'Refreshing…' : 'Refresh' }}
        </Button>
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
        <span v-if="tabCount(tab) != null" class="segmented-item__count">{{ tabCount(tab) }}</span>
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

<style scoped>
/* Refresh belongs beside the title, not baseline-aligned with the wrapped
   subtitle two lines below it. */
.page-header { align-items: flex-start; }
.page-actions { margin-top: 1.125rem; }

.live-pill {
  align-items: center;
  background: var(--color-emerald-50);
  border: 1px solid var(--color-emerald-200);
  border-radius: 9999px;
  color: var(--color-emerald-700);
  display: inline-flex;
  font-size: 0.6875rem;
  font-weight: 700;
  gap: 0.375rem;
  letter-spacing: 0;
  padding: 0.125rem 0.5625rem;
}
.live-pill__dot {
  background: var(--color-emerald-500);
  border-radius: 9999px;
  height: 0.375rem;
  width: 0.375rem;
  animation: live-pulse 2s ease-in-out infinite;
}
@keyframes live-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}
@media (prefers-reduced-motion: reduce) {
  .live-pill__dot { animation: none; }
}

.quiet-btn {
  align-items: center;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 0.5625rem;
  color: var(--color-fg-muted);
  cursor: pointer;
  display: inline-flex;
  font-size: 0.78125rem;
  font-weight: 600;
  gap: 0.4375rem;
  height: 2.125rem;
  padding: 0 0.875rem;
  transition: border-color 0.15s ease, color 0.15s ease;
}
.quiet-btn:hover:not(:disabled) {
  border-color: var(--color-border-strong);
  color: var(--color-fg);
}
.quiet-btn:disabled { cursor: progress; opacity: 0.7; }
.quiet-btn:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 2px; }
.quiet-btn__spin { animation: quiet-spin 0.9s linear infinite; }
@keyframes quiet-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .quiet-btn__spin { animation: none; }
}

.segmented-item__count {
  background: var(--color-surface-3);
  border-radius: 9999px;
  color: var(--color-fg-subtle);
  font-size: 0.6875rem;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  margin-left: 0.4375rem;
  padding: 0.0625rem 0.375rem;
}
.segmented-item.is-active .segmented-item__count {
  background: var(--color-blue-100);
  color: var(--color-blue-800);
}
</style>
