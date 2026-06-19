<script setup>
import { ref, onMounted, computed, watch } from 'vue'
import api from '../api'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'
import Button from '../components/ui/Button.vue'
import HookCard from '../components/HookCard.vue'
import HookLifecycleDiagram from '../components/HookLifecycleDiagram.vue'
import ToggleSwitch from '../components/ToggleSwitch.vue'
import ListInput from '../components/ListInput.vue'
import SettingsRuleTriggers from '../components/SettingsRuleTriggers.vue'
import SettingsBlock from '../components/SettingsBlock.vue'
import SettingsProviders from '../components/SettingsProviders.vue'
import { useFlash } from '../composables/useFlash'
import { useFeatures } from '../composables/useFeatures'
import { useConfirm } from '../composables/useConfirm'
import { useTabRoute } from '../composables/useTabRoute'

const { flash } = useFlash()
const { refresh: refreshFeatures } = useFeatures()
const { confirm } = useConfirm()
const currentUser = api.getStoredUser ? api.getStoredUser() : null
const isAdmin = computed(() => currentUser?.role === 'admin')

function coerceBool(v) {
  if (typeof v === 'boolean') return v
  if (typeof v === 'number') return v !== 0
  if (typeof v === 'string') return ['true', '1', 'yes', 'on'].includes(v.trim().toLowerCase())
  return false
}
const settings = ref([])
const formData = ref({})
const loading = ref(true)

const hooks = ref({})
const hooksLoading = ref({})
const providerHandlers = ref({})
const providerConfigPaths = ref({})
const providerSupportedEvents = ref({})
const selectedProvider = ref('claude')
const handlerLoading = ref({})

const SECTIONS = ['config', 'providers', 'hooks', 'install', 'triggers', 'agent-memory', 'agent-messages', 'debug']
const activeSection = useTabRoute({ param: 'section', default: 'config', valid: SECTIONS })

// ── Nested settings blocks (Agent Memory, Agent Messages) ──────
// Keyed by API name → { fields, form, saving }. One reactive bag + one set
// of generic load/save fns keeps this already-large view's top-level surface
// area flat no matter how many blocks are added.
const blocks = ref({})

const BLOCK_META = {
  'agent-memory': {
    title: 'Agent Memory',
    description: 'How sessions are distilled into memories, how recall ranks them, and when stale ones are retired. Saved to the shared agent_memory config; applies on the next memory operation (no restart).',
  },
  'agent-messages': {
    title: 'Agent Messages',
    description: 'The send_to_user → human channel. The webhook pushes high-severity messages (ntfy / Slack / phone) and is off until a URL is set. Stored machine-local, since the URL can carry a secret token.',
  },
}

// ── Rule trigger thresholds section ────────────────────────────
const triggerThresholds = ref(null)            // last known persisted values
const triggerThresholdsForm = ref(null)        // edited form copy
const triggerStats = ref(null)                 // {total, oldest_at, distinct_rules}
const triggerPreview = ref(null)               // {noisy, dead, active, configured} after save
const triggerSaving = ref(false)
const triggerResetting = ref(false)

// Retention policy — `0` means wipe everything; positive N means
// delete rows older than N days. Matches the windows returned by
// /api/triggers/stats.older_than (7, 30, 90, 365).
const triggerResetPolicy = ref(0)
const TRIGGER_RESET_OPTIONS = [
  { value: 7,   label: 'Older than 7 days' },
  { value: 30,  label: 'Older than 30 days' },
  { value: 90,  label: 'Older than 90 days' },
  { value: 365, label: 'Older than 1 year' },
  { value: 0,   label: 'All time' },
]

async function loadTriggerSettings() {
  const [thresh, stats] = await Promise.all([
    api.get('/settings/rule-triggers/thresholds'),
    api.get('/triggers/stats'),
  ])
  triggerThresholds.value = thresh
  triggerThresholdsForm.value = { ...thresh }
  triggerStats.value = stats
}

async function saveTriggerThresholds() {
  triggerSaving.value = true
  try {
    const res = await api.put(
      '/settings/rule-triggers/thresholds',
      triggerThresholdsForm.value,
    )
    if (!res.ok) {
      flash(res.errors ? res.errors.join('; ') : 'Failed to save', 'error')
      return
    }
    triggerThresholds.value = res.thresholds
    flash('Thresholds saved.')
    // Re-fetch the rule list to surface drift in the inline preview.
    const list = await api.get('/triggers/rules')
    triggerPreview.value = list.kpis
  } finally {
    triggerSaving.value = false
  }
}

function onSelectTriggers() {
  activeSection.value = 'triggers'
}

async function loadBlock(name) {
  const data = await api.get(`/settings/${name}`)
  const fields = data.fields || []
  const form = {}
  for (const f of fields) form[f.key] = f.value
  blocks.value = { ...blocks.value, [name]: { fields, form, saving: false } }
}

function onSelectBlock(name) {
  activeSection.value = name
}

async function saveBlock(name) {
  const b = blocks.value[name]
  if (!b) return
  b.saving = true
  try {
    const res = await api.put(`/settings/${name}`, b.form)
    if (!res.ok) {
      flash(res.errors ? res.errors.join('; ') : (res.error || 'Failed to save'), 'error')
      return
    }
    flash(res.msg || 'Saved')
    await loadBlock(name)
  } finally {
    b.saving = false
  }
}

// Rows that would be deleted at the currently-selected policy.
// `older_than` is a {7: N, 30: N, 90: N, 365: N} map from /stats;
// policy=0 (All time) just shows the grand total.
const triggerResetCount = computed(() => {
  if (!triggerStats.value) return 0
  const p = triggerResetPolicy.value
  if (p === 0) return triggerStats.value.total ?? 0
  return triggerStats.value.older_than?.[p] ?? 0
})

const triggerResetLabel = computed(() => (
  TRIGGER_RESET_OPTIONS.find(o => o.value === triggerResetPolicy.value)?.label
  || 'All time'
))

async function resetTriggerLog() {
  const policy = triggerResetPolicy.value
  const n = triggerResetCount.value
  const scope = policy === 0
    ? 'every row in rule_triggers'
    : `rule_triggers older than ${policy} day${policy === 1 ? '' : 's'}`
  const ok = await confirm(
    'Reset trigger log',
    `Delete ${scope}? This removes ${n.toLocaleString()} event(s) and cannot be undone.`,
    true,
  )
  if (!ok) return
  triggerResetting.value = true
  try {
    const body = policy > 0 ? { older_than_days: policy } : {}
    const res = await api.post('/triggers/reset', body)
    if (!res.ok) {
      flash(res.msg || res.error || 'Failed to reset', 'error')
      return
    }
    flash(res.msg)
    await loadTriggerSettings()
  } finally {
    triggerResetting.value = false
  }
}

async function loadHookState() {
  hooks.value = await api.get('/hooks')
  const providers = hooks.value.providers || []
  if (providers.length && !providers.some(p => p.id === selectedProvider.value)) {
    selectedProvider.value = providers[0].id
  }
  for (const provider of providers) {
    const data = await api.get(`/hooks/handlers?provider=${encodeURIComponent(provider.id)}`)
    providerHandlers.value[provider.id] = data.handlers || []
    providerConfigPaths.value[provider.id] = data.config_path || ''
    providerSupportedEvents.value[provider.id] = data.supported_events || []
  }
}

onMounted(async () => {
  settings.value = await api.get('/settings')
  for (const s of settings.value) {
    if (s.is_list) formData.value[s.key] = [...s.value]
    else if (s.is_bool) formData.value[s.key] = coerceBool(s.value)
    else formData.value[s.key] = s.value
  }
  await loadHookState()
  loading.value = false
})

async function toggleHandler(name) {
  const providerId = selectedProvider.value
  const key = `${providerId}:${name}`
  handlerLoading.value[key] = true
  const result = await api.post(`/hooks/handlers/${name}/toggle?provider=${encodeURIComponent(providerId)}`)
  if (!result.ok) {
    flash(result.msg || 'Toggle failed', 'error')
    handlerLoading.value[key] = false
    return
  }
  flash(result.msg)
  const h = (providerHandlers.value[providerId] || []).find(x => x.name === name)
  if (h) h.enabled = result.enabled
  handlerLoading.value[key] = false
}

async function setHandlerPriority({ name, priority }) {
  const providerId = selectedProvider.value
  const result = await api.post(
    `/hooks/handlers/${name}/priority?provider=${encodeURIComponent(providerId)}`,
    { priority },
  )
  if (!result.ok) {
    flash(result.msg || 'Priority update failed', 'error')
  } else {
    flash(result.msg)
  }
  // Refetch unconditionally so the input field re-syncs with the
  // canonical value on disk (success → confirms write, failure →
  // resets a rejected typo back to what the server believes).
  const data = await api.get(`/hooks/handlers?provider=${encodeURIComponent(providerId)}`)
  providerHandlers.value[providerId] = data.handlers || []
  providerConfigPaths.value[providerId] = data.config_path || ''
}

async function resetHandlerPriority(name) {
  const providerId = selectedProvider.value
  const result = await api.post(
    `/hooks/handlers/${name}/reset-priority?provider=${encodeURIComponent(providerId)}`,
  )
  if (!result.ok) {
    flash(result.msg || 'Reset failed', 'error')
    return
  }
  flash(result.msg)
  const data = await api.get(`/hooks/handlers?provider=${encodeURIComponent(providerId)}`)
  providerHandlers.value[providerId] = data.handlers || []
  providerConfigPaths.value[providerId] = data.config_path || ''
}

const hookProviders = computed(() => hooks.value.providers || [])

const handlers = computed(() => providerHandlers.value[selectedProvider.value] || [])

const configPath = computed(() => providerConfigPaths.value[selectedProvider.value] || '')

const handlersByEvent = computed(() => {
  const groups = {}
  for (const h of handlers.value) {
    for (const ev of h.events) {
      if (!groups[ev]) groups[ev] = []
      groups[ev].push(h)
    }
  }
  for (const ev of Object.keys(groups)) {
    groups[ev].sort((a, b) => a.priority - b.priority || a.name.localeCompare(b.name))
  }
  return groups
})

const enabledHandlerCount = computed(() => handlers.value.filter(h => h.enabled).length)

async function save() {
  const result = await api.post('/settings', formData.value)
  if (!result.ok) { flash(result.msg || 'Failed to save settings', 'error'); return }
  flash(result.msg || 'Saved')
  await refreshFeatures()
}

async function toggleHook(providerId, name) {
  const key = `${providerId}:${name}`
  hooksLoading.value[key] = true
  const provider = hookProviders.value.find(p => p.id === providerId)
  const isInstalled = provider?.[name]?.installed
  const result = await api.post(`/hooks/${name}/${isInstalled ? 'uninstall' : 'install'}?provider=${encodeURIComponent(providerId)}`)
  if (!result.ok) {
    flash(result.msg || 'Hook operation failed', 'error')
    hooksLoading.value[key] = false
    return
  }
  flash(result.msg)
  await loadHookState()
  hooksLoading.value[key] = false
}

const hookDefinitions = [
  {
    key: 'hook_manager',
    title: 'Hook Manager',
    subtitle: 'Recommended',
    description: 'Installs the unified hook dispatcher for this provider. This is what makes the handler toggles above active.'
  },
  {
    key: 'debug',
    title: 'Debug Hook',
    subtitle: 'Optional payload logger',
    description: 'Logs raw hook payloads for this provider. It does <strong>not</strong> enable the handler toggles above.'
  },
]

const showDiagram = ref(false)

const debugPayloads = ref([])
const debugPayloadsLoading = ref(false)

async function fetchDebugPayloads() {
  debugPayloadsLoading.value = true
  const data = await api.get(`/debug-hook-payloads?provider=${encodeURIComponent(selectedProvider.value)}`)
  debugPayloads.value = data.payloads || []
  debugPayloadsLoading.value = false
}

// Lazy-load each section's data the first time it becomes active — driven off
// the route-backed `activeSection`, so a deep link (e.g. /settings?section=triggers)
// loads exactly like clicking the sidebar item. selectedProvider is watched too
// so switching providers re-fetches the debug payloads.
watch([activeSection, selectedProvider], ([section]) => {
  if (section === 'triggers') {
    if (triggerThresholds.value == null) loadTriggerSettings()
  } else if (BLOCK_META[section]) {
    if (!blocks.value[section]) loadBlock(section)
  } else if (section === 'debug') {
    fetchDebugPayloads()
  }
}, { immediate: true })
</script>

<template>
  <div v-if="loading" class="empty-state">Loading settings…</div>
  <div v-else>
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">System</div>
        <h1 class="page-title">Settings</h1>
        <p class="page-subtitle">Team and machine-local configuration, hook handlers, and installers.</p>
      </div>
    </header>
  <div class="sv-layout">

    <!-- Sidebar -->
    <aside class="sv-sidebar">
      <div class="sv-sidebar-heading">Settings</div>
      <nav class="sv-nav">
        <button
          type="button"
          class="sv-nav-item focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-1"
          :class="{ active: activeSection === 'config' }"
          @click="activeSection = 'config'"
        >
          Configuration
        </button>

        <button
          type="button"
          class="sv-nav-item focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-1"
          :class="{ active: activeSection === 'providers' }"
          @click="activeSection = 'providers'"
        >
          Providers
        </button>

        <button
          type="button"
          class="sv-nav-item focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-1"
          :class="{ active: activeSection === 'hooks' }"
          @click="activeSection = 'hooks'"
        >
          <span class="flex-1 text-left">Hook Handlers</span>
          <span v-if="enabledHandlerCount" class="sv-pill">{{ enabledHandlerCount }}</span>
        </button>

        <button
          type="button"
          class="sv-nav-item focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-1"
          :class="{ active: activeSection === 'install' }"
          @click="activeSection = 'install'"
        >
          Hook Installers
        </button>

        <button
          type="button"
          class="sv-nav-item focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-1"
          :class="{ active: activeSection === 'triggers' }"
          @click="onSelectTriggers"
        >
          Rule Triggers
        </button>

        <button
          type="button"
          class="sv-nav-item focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-1"
          :class="{ active: activeSection === 'agent-memory' }"
          @click="onSelectBlock('agent-memory')"
        >
          Agent Memory
        </button>

        <button
          type="button"
          class="sv-nav-item focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-1"
          :class="{ active: activeSection === 'agent-messages' }"
          @click="onSelectBlock('agent-messages')"
        >
          Agent Messages
        </button>

        <button
          type="button"
          class="sv-nav-item focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-1"
          :class="{ active: activeSection === 'debug' }"
          @click="activeSection = 'debug'"
        >
          Payload Debugger
        </button>
      </nav>
    </aside>

    <!-- Content pane -->
    <div class="sv-content">

      <!-- Configuration -->
      <template v-if="activeSection === 'config'">
        <div class="sv-section-header">
          <h2 class="sv-section-title">Configuration</h2>
          <p class="sv-section-desc">Manage team and machine-local settings. Team settings are versioned with git; local settings are machine-specific overrides.</p>
        </div>

        <div class="sv-group">
          <div class="sv-group-label">Team Settings</div>
          <p class="sv-group-meta">Shared across the team via git.</p>
          <Card :no-padding="true">
            <table class="tbl">
              <thead><tr><th>Setting</th><th>Value</th><th>Default</th></tr></thead>
              <tbody>
                <tr v-for="s in settings.filter(s => s.scope === 'shared')" :key="s.key">
                  <td>
                    <div class="font-medium text-gray-900">{{ s.key }}</div>
                    <div class="text-xs text-gray-400 mt-0.5">{{ s.description }}</div>
                  </td>
                  <td>
                    <ListInput v-if="s.is_list" v-model="formData[s.key]" />
                    <ToggleSwitch v-else-if="s.is_bool" v-model="formData[s.key]" :aria-label="s.key" />
                    <input v-else type="text" v-model="formData[s.key]" :aria-label="s.key" :placeholder="String(s.default)"
                           class="text-sm border border-gray-300 rounded-md px-2.5 py-1.5 w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500">
                  </td>
                  <td class="text-xs text-gray-400">
                    <code class="text-xs">{{ s.is_list ? s.default.join(', ') : s.default }}</code>
                    <Badge v-if="s.overridden" color="blue" label="overridden" class="ml-1" />
                  </td>
                </tr>
              </tbody>
            </table>
          </Card>
        </div>

        <div class="sv-group mt-6">
          <div class="sv-group-label">Local Settings</div>
          <p class="sv-group-meta">Machine-specific paths. Not shared with the team.</p>
          <Card :no-padding="true">
            <table class="tbl">
              <thead><tr><th>Setting</th><th>Value</th><th>Default</th></tr></thead>
              <tbody>
                <tr v-for="s in settings.filter(s => s.scope === 'local')" :key="s.key">
                  <td>
                    <div class="font-medium text-gray-900">{{ s.key }}</div>
                    <div class="text-xs text-gray-400 mt-0.5">{{ s.description }}</div>
                  </td>
                  <td>
                    <ListInput v-if="s.is_list" v-model="formData[s.key]" />
                    <ToggleSwitch v-else-if="s.is_bool" v-model="formData[s.key]" :aria-label="s.key" />
                    <input v-else type="text" v-model="formData[s.key]" :aria-label="s.key" :placeholder="String(s.default)"
                           class="text-sm border border-gray-300 rounded-md px-2.5 py-1.5 w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500">
                  </td>
                  <td class="text-xs text-gray-400">
                    <code class="text-xs">{{ s.is_list ? s.default.join(', ') : s.default }}</code>
                    <Badge v-if="s.overridden" color="blue" label="overridden" class="ml-1" />
                  </td>
                </tr>
              </tbody>
            </table>
          </Card>
        </div>

        <div class="mt-5 flex items-center gap-3">
          <Button variant="primary" @click="save">Save settings</Button>
        </div>
      </template>

      <!-- Providers -->
      <template v-else-if="activeSection === 'providers'">
        <SettingsProviders />
      </template>

      <!-- Rule Triggers -->
      <template v-else-if="activeSection === 'triggers'">
        <SettingsRuleTriggers
          :is-admin="isAdmin"
          :thresholds-form="triggerThresholdsForm"
          :saving="triggerSaving"
          :preview="triggerPreview"
          :stats="triggerStats"
          v-model:reset-policy="triggerResetPolicy"
          :reset-options="TRIGGER_RESET_OPTIONS"
          :resetting="triggerResetting"
          :reset-count="triggerResetCount"
          :reset-label="triggerResetLabel"
          @save-thresholds="saveTriggerThresholds"
          @reset-log="resetTriggerLog"
        />
      </template>

      <!-- Nested settings blocks (Agent Memory, Agent Messages) -->
      <template v-else-if="BLOCK_META[activeSection]">
        <SettingsBlock
          :title="BLOCK_META[activeSection].title"
          :description="BLOCK_META[activeSection].description"
          :fields="blocks[activeSection]?.fields || []"
          :form="blocks[activeSection]?.form || null"
          :saving="blocks[activeSection]?.saving || false"
          @save="saveBlock(activeSection)"
        />
      </template>

      <!-- Hook Handlers -->
      <template v-else-if="activeSection === 'hooks'">
        <div class="sv-section-header">
          <div class="flex items-start justify-between gap-3">
            <h2 class="sv-section-title">Hook Handlers</h2>
            <!-- Diagram toggle lives in the section header as a view
                 switcher, intentionally separate from the per-provider
                 tab row below. Diagram contents *are* provider-aware
                 (they read whatever provider's handlers are selected),
                 but the toggle itself is section-scoped, not per-tab. -->
            <button
              type="button"
              class="sv-view-toggle focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-1"
              :class="{ 'is-active': showDiagram }"
              :aria-pressed="showDiagram"
              @click="showDiagram = !showDiagram"
            >
              <span aria-hidden="true">⊞</span>
              {{ showDiagram ? 'Hide diagram' : 'Show diagram' }}
            </button>
          </div>
          <p class="sv-section-desc">Enable or disable individual handlers per provider. Handlers are only active once hook_manager is installed for that provider.</p>
        </div>

        <div class="flex flex-wrap gap-2 mb-4">
          <Button
            v-for="provider in hookProviders"
            :key="provider.id"
            size="sm"
            :variant="selectedProvider === provider.id ? 'primary' : 'secondary'"
            @click="selectedProvider = provider.id"
          >
            {{ provider.name }}
          </Button>
        </div>

        <div v-if="showDiagram" class="mb-6">
          <HookLifecycleDiagram
            :handlers="handlers"
            :handlers-by-event="handlersByEvent"
            :handler-loading="handlerLoading"
            :selected-provider="selectedProvider"
            :supported-events="providerSupportedEvents[selectedProvider] || []"
            @toggle-handler="toggleHandler"
            @set-priority="setHandlerPriority"
            @reset-priority="resetHandlerPriority"
          />
        </div>

        <Card v-if="configPath" class="mb-4">
          <div class="flex items-start justify-between gap-4">
            <div class="min-w-0">
              <h3 class="text-sm font-semibold text-gray-800">Persistence</h3>
              <div class="text-xs text-gray-500 mt-0.5 break-all"><code>{{ configPath }}</code></div>
              <p class="text-xs text-gray-500 mt-2 leading-snug">
                Handler enable/disable flags and priority overrides for the selected provider are stored in this JSON file. Edits made here take effect on the next hook fire — each event invokes a fresh <code>python -m hook_manager</code> subprocess, so no server restart is needed.
              </p>
            </div>
            <Badge color="gray" label="JSON" />
          </div>
        </Card>

        <p v-if="!handlers.some(h => h.wired)" class="text-sm text-amber-700 mb-4">
          Hook manager is not installed for {{ hookProviders.find(p => p.id === selectedProvider)?.name || selectedProvider }} right now, so these handlers are configured defaults only.
        </p>

        <Card :no-padding="true">
          <table class="tbl">
            <thead><tr><th>Handler</th><th>Events</th><th>Kind</th><th class="text-right">Status</th></tr></thead>
            <tbody>
              <tr v-for="h in handlers" :key="h.name">
                <td>
                  <div class="font-medium text-gray-900">{{ h.label }}</div>
                  <div v-if="h.summary" class="text-xs text-gray-500 mt-0.5">{{ h.summary }}</div>
                  <div class="text-xs text-gray-400 mt-1"><code>{{ h.name }}</code> · priority {{ h.priority }}</div>
                </td>
                <td class="text-xs text-gray-600">
                  <div>{{ h.events.join(', ') }}</div>
                  <div v-if="h.wired_events?.length" class="text-gray-500 mt-0.5">installed on: {{ h.wired_events.join(', ') }}</div>
                  <div v-if="h.match_hint" class="text-gray-400 mt-0.5">{{ h.match_hint }}</div>
                </td>
                <td>
                  <Badge :color="h.kind === 'gate' ? 'red' : h.kind === 'notify' ? 'purple' : h.kind === 'enrich' ? 'blue' : 'gray'" :label="h.kind" />
                </td>
                <td class="text-right">
                  <div class="inline-flex justify-end w-full">
                    <ToggleSwitch
                      :model-value="h.enabled"
                      :loading="handlerLoading[`${selectedProvider}:${h.name}`]"
                      :disabled="!h.wired"
                      on-label="Enabled"
                      :off-label="h.wired ? 'Disabled' : 'Not wired'"
                      @change="toggleHandler(h.name)"
                    />
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </Card>
      </template>

      <!-- Hook Installers -->
      <template v-else-if="activeSection === 'install'">
        <div class="sv-section-header">
          <h2 class="sv-section-title">Hook Installers</h2>
          <p class="sv-section-desc">Install Hook Manager separately for each provider. The debug hook is optional and only logs raw payloads.</p>
        </div>

        <div class="space-y-4">
          <Card v-for="provider in hookProviders" :key="provider.id">
            <div class="flex items-start justify-between gap-4 mb-3">
              <div>
                <h3 class="text-sm font-semibold text-gray-800">{{ provider.name }}</h3>
                <div class="text-xs text-gray-500 mt-0.5"><code>{{ provider.hook_settings_path }}</code></div>
              </div>
              <Badge :color="provider.hooks_supported ? 'green' : 'gray'" :label="provider.hooks_supported ? 'hooks supported' : 'not supported'" />
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
                @toggle="toggleHook(provider.id, h.key)"
              />
            </div>
          </Card>
        </div>
      </template>

      <!-- Payload Debugger -->
      <template v-else-if="activeSection === 'debug'">
        <div class="sv-section-header">
          <h2 class="sv-section-title">Payload Debugger</h2>
          <p class="sv-section-desc">Inspect raw hook payloads for the selected provider. Useful when building new hooks.</p>
        </div>

        <div class="flex items-center gap-3 mb-4">
          <Button variant="secondary" size="sm" @click="fetchDebugPayloads">Refresh payloads</Button>
          <span v-if="debugPayloadsLoading" class="text-xs text-gray-400">loading…</span>
          <span v-else-if="debugPayloads.length" class="text-xs text-gray-400">{{ debugPayloads.length }} entries</span>
        </div>

        <div v-if="debugPayloads.length" class="space-y-2">
          <Card v-for="entry in debugPayloads.slice().reverse()" :key="entry.received_at" class="text-xs">
            <div class="flex items-center gap-2 mb-1">
              <Badge :color="entry.payload?.hook_event_name === 'UserPromptSubmit' ? 'purple' : 'blue'" :label="entry.payload?.hook_event_name || 'Unknown'" />
              <span class="text-gray-400">{{ new Date(entry.received_at).toLocaleString() }}</span>
            </div>
            <pre class="bg-gray-50 p-2 rounded overflow-x-auto text-[11px]"><code>{{ JSON.stringify(entry.payload, null, 2) }}</code></pre>
          </Card>
        </div>
        <div v-else class="text-sm text-slate-400">No payloads logged yet. Install the debug hook and trigger some Claude Code events.</div>
      </template>

    </div>
  </div>
  </div>
</template>

<style scoped>
.sv-layout {
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: 0;
  border: 1px solid var(--color-slate-100);
  border-radius: 0.875rem;
  overflow: hidden;
  background: var(--color-white);
  min-height: 480px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
}
.sv-sidebar {
  border-right: 1px solid var(--color-slate-100);
  padding: 1.25rem 0.75rem;
  background: var(--color-slate-50);
}
.sv-sidebar-heading {
  font-size: 0.625rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--color-slate-400);
  padding: 0 0.75rem;
  margin-bottom: 0.625rem;
}
.sv-nav {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}
.sv-nav-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  width: 100%;
  padding: 0.5rem 0.75rem;
  border-radius: 0.625rem;
  font-size: 0.8125rem;
  color: var(--color-slate-600);
  background: none;
  border: none;
  cursor: pointer;
  transition: background-color 150ms, color 150ms;
  text-align: left;
}
.sv-nav-item:hover {
  background: var(--color-slate-200);
  color: var(--color-slate-900);
}
.sv-nav-item.active {
  background: linear-gradient(135deg, var(--color-blue-800), var(--color-blue-500));
  color: #fff;
  font-weight: 500;
  box-shadow: 0 4px 12px rgba(30, 64, 175, 0.2);
}
.sv-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 1.25rem;
  height: 1.25rem;
  padding: 0 0.375rem;
  border-radius: 9999px;
  background: var(--color-slate-100);
  font-size: 0.625rem;
  font-weight: 600;
  color: var(--color-slate-500);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.sv-pill-green { background: var(--color-green-100); color: var(--color-green-700); }
.sv-pill-red   { background: var(--color-red-100); color: var(--color-red-700); }
.sv-pill-gray  { background: var(--color-slate-100); color: var(--color-slate-400); }
.sv-nav-item.active .sv-pill { background: rgba(255, 255, 255, 0.2); color: #fff; }
.sv-content {
  padding: 1.75rem 2rem;
  min-width: 0;
  overflow: auto;
}
.sv-view-toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.25rem 0.625rem;
  border: 1px solid var(--color-slate-200);
  border-radius: 0.375rem;
  background: var(--color-white);
  font-size: 0.75rem;
  color: var(--color-slate-600);
  cursor: pointer;
  transition: background-color 0.12s, border-color 0.12s, color 0.12s;
  white-space: nowrap;
}
.sv-view-toggle:hover {
  border-color: var(--color-slate-300);
  color: var(--color-slate-800);
}
.sv-view-toggle.is-active {
  border-color: var(--color-blue-600);
  background: var(--color-blue-50);
  color: var(--color-blue-700);
}
.sv-view-toggle:focus-visible {
  outline: 2px solid var(--color-blue-600);
  outline-offset: 2px;
}
</style>
