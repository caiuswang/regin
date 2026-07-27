<script setup>
import { computed, ref } from 'vue'
import Badge from './Badge.vue'
import Button from './ui/Button.vue'

const props = defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
  description: { type: String, required: true },
  installed: { type: Boolean, default: null },
  loading: { type: Boolean, default: false },
  // Installed, but at least one routed command differs from what install
  // would write today (or an expected event is unrouted). Without this the
  // card cannot tell a healthy install from one that needs rewriting.
  stale: { type: Boolean, default: false },
  routedEvents: { type: Array, default: () => [] },
  commands: { type: Object, default: () => ({}) },
  expectedCommands: { type: Object, default: () => ({}) },
  staleEvents: { type: Array, default: () => [] },
  missingEvents: { type: Array, default: () => [] },
})

const emit = defineEmits(['toggle', 'refresh'])

const showWiring = ref(false)

const state = computed(() => {
  if (!props.installed) return { color: 'gray', label: 'Not installed' }
  return props.stale
    ? { color: 'yellow', label: 'Needs repair' }
    : { color: 'green', label: 'Installed' }
})

const eventRows = computed(() => {
  const events = new Set([...props.routedEvents, ...Object.keys(props.expectedCommands)])
  return [...events].sort().map(event => ({
    event,
    current: (props.commands[event] || []).join('\n'),
    expected: props.expectedCommands[event] || '',
    stale: props.staleEvents.includes(event),
    missing: props.missingEvents.includes(event),
  }))
})
</script>

<template>
  <div class="card">
    <div class="flex items-start justify-between gap-3">
      <div class="min-w-0">
        <div class="font-medium text-gray-900">
          {{ title }}
          <span v-if="subtitle" class="text-gray-400 font-normal">&mdash; {{ subtitle }}</span>
        </div>
        <div class="text-xs text-gray-400 mt-0.5" v-html="description"></div>
        <div class="text-xs mt-1 flex items-center gap-2 flex-wrap">
          <span v-if="installed === null || loading" class="text-gray-400">Checking...</span>
          <Badge v-else :color="state.color" :label="state.label" />
          <span v-if="installed && !loading" class="text-gray-400">
            {{ routedEvents.length }} event{{ routedEvents.length === 1 ? '' : 's' }} routed
          </span>
          <Button
            v-if="installed && !loading"
            variant="link"
            size="sm"
            @click="showWiring = !showWiring"
          >{{ showWiring ? 'Hide' : 'Show' }} commands</Button>
        </div>
        <p v-if="stale && !loading" class="text-xs text-amber-700 mt-1">
          The installed command is not the one regin writes today. Refresh to rewrite it.
        </p>
      </div>
      <div v-if="installed !== null && !loading" class="flex gap-2 shrink-0">
        <template v-if="installed">
          <Button :variant="stale ? 'primary' : 'secondary'" size="sm" @click="emit('refresh')">Refresh</Button>
          <Button variant="secondary" size="sm" @click="emit('toggle')">Remove</Button>
        </template>
        <Button v-else variant="primary" size="sm" @click="emit('toggle')">Install</Button>
      </div>
    </div>

    <div v-if="showWiring && installed" class="mt-3 border-t border-border pt-2 space-y-2">
      <div v-for="row in eventRows" :key="row.event" class="text-xs">
        <div class="flex items-center gap-2">
          <span class="font-mono text-gray-700">{{ row.event }}</span>
          <Badge v-if="row.missing" color="yellow" label="not routed" />
          <Badge v-else-if="row.stale" color="yellow" label="stale" />
        </div>
        <pre v-if="row.current" class="text-[11px] text-gray-500 whitespace-pre-wrap break-all">{{ row.current }}</pre>
        <pre
          v-if="row.stale || row.missing"
          class="text-[11px] text-emerald-700 whitespace-pre-wrap break-all"
        >{{ row.expected }}</pre>
      </div>
    </div>
  </div>
</template>
