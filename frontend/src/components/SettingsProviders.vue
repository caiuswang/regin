<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../api'
import Card from './Card.vue'
import Badge from './Badge.vue'
import Button from './ui/Button.vue'
import Select from './ui/Select.vue'
import ToggleSwitch from './ToggleSwitch.vue'
import { useFlash } from '../composables/useFlash'

// Agent-providers settings section, extracted from SettingsView to match the
// sibling-section pattern (SettingsRuleTriggers / SettingsBlock). Self-contained:
// owns its own state and load/save against /api/settings/providers, and loads
// lazily on mount (the parent renders it only when its tab is active).
const { flash } = useFlash()

const providers = ref([])
const handlerDefaults = ref([])
const selectedProviderConfigId = ref('')
const providerSaving = ref(false)
const newPriorityHandler = ref('')
const newPriorityValue = ref('')

const providerPathFields = [
  'skills_dir',
  'plans_dir',
  'traces_dir',
  'hook_settings_path',
  'hook_manager_config_path',
  'hook_payload_log_path',
  'transcript_projects_dir',
]

const selectedProviderConfig = computed(() =>
  providers.value.find(p => p.id === selectedProviderConfigId.value) || null,
)

const availableHandlers = computed(() =>
  handlerDefaults.value.filter(h =>
    !(selectedProviderConfig.value?.disabled_handlers || []).includes(h.name),
  ),
)

const availablePriorityHandlers = computed(() =>
  handlerDefaults.value.filter(h =>
    !(selectedProviderConfig.value?.priority_overrides || {}).hasOwnProperty(h.name),
  ),
)

async function loadProviders() {
  const data = await api.get('/settings/providers')
  providers.value = data.providers || []
  handlerDefaults.value = data.handler_defaults || []
  if (providers.value.length && !providers.value.some(p => p.id === selectedProviderConfigId.value)) {
    selectedProviderConfigId.value = providers.value[0].id
  }
}

async function saveProviders() {
  providerSaving.value = true
  try {
    const payload = {}
    for (const p of providers.value) {
      const entry = {
        enabled: p.enabled,
        disabled_handlers: p.disabled_handlers,
        priority_overrides: p.priority_overrides,
      }
      // Only send non-empty path overrides; null/blank means "use provider default".
      for (const field of providerPathFields) {
        const val = p.path_overrides[field]
        if (val !== '' && val != null) {
          entry[field] = val
        }
      }
      payload[p.id] = entry
    }
    const res = await api.put('/settings/providers', { providers: payload })
    if (!res.ok) {
      flash(res.errors ? res.errors.join('; ') : (res.error || 'Failed to save'), 'error')
      return
    }
    flash(res.msg || 'Providers saved.')
    await loadProviders()
  } finally {
    providerSaving.value = false
  }
}

function addDisabledHandler(name) {
  if (!name) return
  const p = selectedProviderConfig.value
  if (!p) return
  if (!p.disabled_handlers.includes(name)) {
    p.disabled_handlers.push(name)
  }
}

function removeDisabledHandler(name) {
  const p = selectedProviderConfig.value
  if (!p) return
  p.disabled_handlers = p.disabled_handlers.filter(h => h !== name)
}

function addPriorityOverride() {
  const p = selectedProviderConfig.value
  if (!p || !newPriorityHandler.value) return
  p.priority_overrides[newPriorityHandler.value] = Number(newPriorityValue.value) || 0
  newPriorityHandler.value = ''
  newPriorityValue.value = ''
}

function removePriorityOverride(name) {
  const p = selectedProviderConfig.value
  if (!p) return
  const copy = { ...p.priority_overrides }
  delete copy[name]
  p.priority_overrides = copy
}

onMounted(loadProviders)
</script>

<template>
  <div class="sv-section-header">
    <h2 class="sv-section-title">Agent Providers</h2>
    <p class="sv-section-desc">Enable the providers regin deploys skills and hooks to. The active provider is always enabled; enabling extras makes operations like <em>Push to project</em> deploy to every enabled provider at once.</p>
  </div>

  <div class="flex flex-wrap gap-2 mb-4">
    <Button
      v-for="provider in providers"
      :key="provider.id"
      size="sm"
      class="text-xs"
      :variant="selectedProviderConfigId === provider.id ? 'primary' : 'secondary'"
      @click="selectedProviderConfigId = provider.id"
    >
      {{ provider.name }}
      <span v-if="provider.active" class="sv-pill sv-pill-green ml-1.5">active</span>
      <span v-else-if="provider.enabled" class="sv-pill sv-pill-gray ml-1.5">enabled</span>
    </Button>
  </div>

  <Card v-if="selectedProviderConfig">
    <div class="flex items-start justify-between gap-4 mb-5">
      <div>
        <h3 class="text-sm font-semibold text-gray-800">{{ selectedProviderConfig.name }}</h3>
        <div class="text-xs text-gray-500 mt-0.5"><code>{{ selectedProviderConfig.id }}</code></div>
        <div class="flex flex-wrap gap-1.5 mt-2">
          <Badge v-if="selectedProviderConfig.capabilities.skills" color="green" label="skills" />
          <Badge v-if="selectedProviderConfig.capabilities.hooks" color="blue" label="hooks" />
          <Badge v-if="selectedProviderConfig.capabilities.sessions" color="purple" label="sessions" />
          <Badge v-if="selectedProviderConfig.capabilities.transcript_usage" color="gray" label="transcript" />
        </div>
      </div>
      <div class="flex items-center gap-2">
        <span class="text-xs text-gray-500">{{ selectedProviderConfig.active ? 'Active provider' : 'Enabled' }}</span>
        <ToggleSwitch
          :model-value="selectedProviderConfig.enabled"
          :disabled="selectedProviderConfig.active"
          @update:model-value="selectedProviderConfig.enabled = $event"
        />
      </div>
    </div>

    <h4 class="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-3">Path overrides</h4>
    <div class="space-y-3 mb-6">
      <div v-for="field in providerPathFields" :key="field">
        <label class="block text-xs text-gray-600 mb-1">{{ field.replace(/_/g, ' ') }}</label>
        <input
          type="text"
          v-model="selectedProviderConfig.path_overrides[field]"
          :placeholder="selectedProviderConfig.default_paths[field] || 'default'"
          class="text-sm border border-gray-300 rounded-md px-2.5 py-1.5 w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
        >
        <p class="text-[11px] text-gray-400 mt-0.5">Leave blank to use the provider default.</p>
      </div>
    </div>

    <h4 class="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">Hook handler overrides</h4>
    <p class="text-xs text-gray-500 mb-4">
      These merge on top of the provider's hook-manager-config.json. Disabled handlers are skipped even if enabled in the file; priority overrides take precedence.
    </p>

    <div class="mb-5">
      <label class="block text-xs font-medium text-gray-700 mb-1.5">Disabled handlers</label>
      <div v-if="selectedProviderConfig.disabled_handlers.length" class="flex flex-wrap gap-2 mb-2">
        <span
          v-for="h in selectedProviderConfig.disabled_handlers"
          :key="h"
          class="inline-flex items-center gap-1 bg-red-50 text-red-700 text-xs font-medium px-2 py-0.5 rounded border border-red-100"
        >
          <code>{{ h }}</code>
          <button
            type="button"
            @click="removeDisabledHandler(h)"
            class="text-red-600 hover:text-red-900 focus-visible:outline-2 focus-visible:outline-blue-500"
            :aria-label="`Remove ${h}`"
          >
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        </span>
      </div>
      <Select
        block
        placeholder="Add handler…"
        :options="availableHandlers.map(h => ({ value: h.name, label: `${h.label} (${h.name})` }))"
        @change="addDisabledHandler($event.target.value)"
      />
    </div>

    <div>
      <label class="block text-xs font-medium text-gray-700 mb-1.5">Priority overrides</label>
      <Card v-if="Object.keys(selectedProviderConfig.priority_overrides).length" :no-padding="true" class="mb-2">
        <table class="tbl text-sm">
          <thead><tr><th>Handler</th><th>Priority</th><th></th></tr></thead>
          <tbody>
            <tr v-for="(priority, name) in selectedProviderConfig.priority_overrides" :key="name">
              <td><code class="text-xs">{{ name }}</code></td>
              <td>
                <input
                  type="number"
                  v-model.number="selectedProviderConfig.priority_overrides[name]"
                  class="text-sm border border-gray-300 rounded-md px-2 py-1 w-24 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                >
              </td>
              <td class="text-right">
                <button
                  type="button"
                  class="text-gray-400 hover:text-red-600 text-xs focus-visible:ring-2 focus-visible:ring-blue-500 rounded"
                  @click="removePriorityOverride(name)"
                >Remove</button>
              </td>
            </tr>
          </tbody>
        </table>
      </Card>
      <div class="flex gap-2">
        <span class="flex-1 min-w-0">
          <Select
            block
            v-model="newPriorityHandler"
            placeholder="Handler…"
            :options="availablePriorityHandlers.map(h => ({ value: h.name, label: `${h.label} (default ${h.default_priority})` }))"
          />
        </span>
        <input
          type="number"
          v-model.number="newPriorityValue"
          placeholder="priority"
          class="text-sm border border-gray-300 rounded-md px-2.5 py-1.5 w-28 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
        >
        <Button variant="secondary" size="sm" class="text-xs" @click="addPriorityOverride">Add</Button>
      </div>
    </div>
  </Card>

  <div class="mt-5 flex items-center gap-3">
    <Button variant="primary" :disabled="providerSaving" @click="saveProviders">
      {{ providerSaving ? 'Saving…' : 'Save providers' }}
    </Button>
  </div>
</template>
