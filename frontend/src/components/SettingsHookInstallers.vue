<script setup>
import { ref } from 'vue'
import api from '../api'
import Card from './Card.vue'
import Badge from './Badge.vue'
import HookCard from './HookCard.vue'
import { useFlash } from '../composables/useFlash'

// `reload` is a function prop rather than an event because the card must stay
// disabled until the refetched state lands — an emit cannot be awaited, so the
// buttons would re-enable for a tick still showing pre-refresh state.
const props = defineProps({
  providers: { type: Array, default: () => [] },
  reload: { type: Function, required: true },
})

const { flash } = useFlash()
const hooksLoading = ref({})

const hookDefinitions = [
  {
    key: 'hook_manager',
    title: 'Hook Manager',
    subtitle: 'Recommended',
    description: 'Installs the unified hook dispatcher for this provider. This is what makes the handler toggles above active.',
  },
  {
    key: 'debug',
    title: 'Debug Hook',
    subtitle: 'Optional payload logger',
    description: 'Logs raw hook payloads for this provider. It does <strong>not</strong> enable the handler toggles above.',
  },
]

async function run(providerId, name, action) {
  const key = `${providerId}:${name}`
  hooksLoading.value[key] = true
  try {
    const result = await api.post(`/hooks/${name}/${action}?provider=${encodeURIComponent(providerId)}`)
    if (!result.ok) {
      flash(result.msg || 'Hook operation failed', 'error')
      return
    }
    flash(result.msg)
    await props.reload()
  } finally {
    hooksLoading.value[key] = false
  }
}

function toggleHook(provider, name) {
  run(provider.id, name, provider[name]?.installed ? 'uninstall' : 'install')
}

// Install is idempotent and rewrites a command that has drifted from what
// regin writes today, so repairing stale wiring is the same call as a fresh
// install — no separate endpoint, and no remove-then-reinstall round trip.
function refreshHook(providerId, name) {
  run(providerId, name, 'install')
}
</script>

<template>
  <div class="sv-section-header">
    <h2 class="sv-section-title">Hook Installers</h2>
    <p class="sv-section-desc">
      Install Hook Manager separately for each provider. The debug hook is optional and only logs raw payloads.
      A card marked <em>Needs repair</em> is routed to a command regin no longer writes — <strong>Refresh</strong> rewrites it
      (the CLI equivalent is <code>regin hooks repair</code>). One marked <em>Other checkout</em> is routed to a different
      regin directory — <strong>Adopt</strong> takes it over (<code>regin hooks adopt</code>).
    </p>
  </div>

  <div class="space-y-4">
    <Card v-for="provider in providers" :key="provider.id">
      <div class="flex items-start justify-between gap-4 mb-3">
        <div>
          <h3 class="text-sm font-semibold text-gray-800">{{ provider.name }}</h3>
          <div class="text-xs text-gray-500 mt-0.5"><code>{{ provider.hook_settings_path }}</code></div>
        </div>
        <Badge
          :color="provider.hooks_supported ? 'green' : 'gray'"
          :label="provider.hooks_supported ? 'hooks supported' : 'not supported'"
        />
      </div>
      <div class="space-y-3">
        <HookCard
          v-for="h in hookDefinitions"
          :key="`${provider.id}:${h.key}`"
          :title="h.title"
          :subtitle="h.subtitle"
          :description="h.description"
          :installed="provider[h.key]?.installed ?? null"
          :loading="hooksLoading[`${provider.id}:${h.key}`]"
          :stale="provider[h.key]?.stale ?? false"
          :routed-events="provider[h.key]?.routed_events ?? []"
          :commands="provider[h.key]?.commands ?? {}"
          :expected-commands="provider[h.key]?.expected_commands ?? {}"
          :stale-events="provider[h.key]?.stale_events ?? []"
          :missing-events="provider[h.key]?.missing_events ?? []"
          :foreign-events="provider[h.key]?.foreign_events ?? []"
          :foreign-roots="provider[h.key]?.foreign_roots ?? []"
          @toggle="toggleHook(provider, h.key)"
          @refresh="refreshHook(provider.id, h.key)"
          @adopt="run(provider.id, h.key, 'adopt')"
        />
      </div>
    </Card>
  </div>
</template>
