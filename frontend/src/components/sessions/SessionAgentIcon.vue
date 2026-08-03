<script setup>
import { computed } from 'vue'
import { agentTypeLabel, agentTypeTone } from '../../utils/sessionRowFormat.js'

const props = defineProps({
  s: { type: Object, required: true },
  size: { type: String, default: 'md' },  // 'md' (list) | 'sm' (card)
})

const label = computed(() => agentTypeLabel(props.s))
const tone = computed(() => agentTypeTone(props.s))
</script>

<template>
  <span
    class="agent-icon"
    :class="[`agent-icon--${tone}`, `agent-icon--size-${size}`]"
    :title="label"
    :aria-label="label"
    role="img"
  >
    <svg v-if="tone === 'workflow'" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="3.2" cy="8" r="1.5" />
      <path d="M4.7 8h2.8M7.5 4v8M7.5 4h3M7.5 8h3M7.5 12h3" />
      <circle cx="12" cy="4" r="1.3" />
      <circle cx="12" cy="8" r="1.3" />
      <circle cx="12" cy="12" r="1.3" />
    </svg>
    <svg v-else-if="tone === 'llm-stage'" viewBox="0 0 16 16" aria-hidden="true">
      <rect x="2.5" y="2.5" width="11" height="11" rx="2" />
      <path d="M5.5 8.5 7 10l3.5-3.5" />
    </svg>
    <svg v-else-if="tone === 'claude'" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M8 2.2 9.5 6.5 13.8 8 9.5 9.5 8 13.8 6.5 9.5 2.2 8 6.5 6.5 8 2.2Z" />
    </svg>
    <svg v-else-if="tone === 'codex'" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M3 3.5h10a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1Zm2.2 3L7 8 5.2 9.5M8.2 10h3" />
    </svg>
    <svg v-else-if="tone === 'kimi'" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M5 3v10M5 8l5-5M5 8.5l5 4.5" />
    </svg>
    <svg v-else viewBox="0 0 16 16" aria-hidden="true">
      <path d="M8 2.5a5.5 5.5 0 1 1 0 11 5.5 5.5 0 0 1 0-11Zm0 2.2v3.1l2.2 2.1" />
    </svg>
  </span>
</template>

<style scoped>
.agent-icon {
  align-items: center;
  border-radius: 0.625rem;
  display: inline-flex;
  flex: 0 0 auto;
  justify-content: center;
}
.agent-icon--size-md { height: 1.75rem; width: 1.75rem; }
.agent-icon--size-sm { height: 1.375rem; width: 1.375rem; border-radius: 0.5rem; }
.agent-icon svg {
  fill: none;
  stroke: currentColor;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.6;
}
.agent-icon--size-md svg { height: 1rem; width: 1rem; }
.agent-icon--size-sm svg { height: 0.8125rem; width: 0.8125rem; }

.agent-icon--claude { background: var(--color-orange-50); color: var(--color-orange-700); }
.agent-icon--codex { background: var(--color-indigo-50); color: var(--color-indigo-700); }
.agent-icon--kimi { background: var(--color-purple-50); color: var(--color-purple-700); }
.agent-icon--generic { background: var(--color-surface-2); color: var(--color-fg-subtle); }
.agent-icon--workflow { background: var(--color-emerald-50); color: var(--color-teal-700); }
.agent-icon--llm-stage { background: var(--color-sky-50); color: var(--color-sky-700); }
</style>
