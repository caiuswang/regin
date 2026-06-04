<script setup>
import Card from './Card.vue'
import Badge from './Badge.vue'

defineProps({
  isAdmin: { type: Boolean, default: false },
  thresholdsForm: { type: Object, default: null },
  saving: { type: Boolean, default: false },
  preview: { type: Object, default: null },
  stats: { type: Object, default: null },
  resetPolicy: { type: Number, default: 0 },
  resetOptions: { type: Array, default: () => [] },
  resetting: { type: Boolean, default: false },
  resetCount: { type: Number, default: 0 },
  resetLabel: { type: String, default: 'All time' },
})

defineEmits(['update:resetPolicy', 'save-thresholds', 'reset-log'])
</script>

<template>
  <div class="sv-section-header">
    <h2 class="sv-section-title">Rule Triggers</h2>
    <p class="sv-section-desc">Thresholds that decide which rules read as <strong>noisy</strong>, <strong>active</strong>, or <strong>dead</strong> on the
      <router-link to="/trace/triggers" class="text-blue-700 hover:underline focus-visible:ring-2 focus-visible:ring-blue-500">Trace › Rule Triggers</router-link>
      tab. Plus admin-only retention controls for the trigger log.</p>
  </div>

  <div class="sv-group">
    <div class="sv-group-label">Health classification thresholds</div>
    <p class="sv-group-meta">A rule is <strong>noisy</strong> when its trigger rate AND its fire count both clear the gates below. <strong>Dead</strong> means zero fires across at least the configured number of checks.</p>
    <Card>
      <div v-if="!thresholdsForm" class="text-sm text-gray-500">Loading…</div>
      <div v-else class="space-y-3">
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label class="block">
            <span class="text-xs text-gray-600">noisy_min_rate_pct (0–100)</span>
            <input
              type="number" min="0" max="100"
              v-model.number="thresholdsForm.noisy_min_rate_pct"
              class="mt-1 text-sm border border-gray-300 rounded-md px-2.5 py-1.5 w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
            />
          </label>
          <label class="block">
            <span class="text-xs text-gray-600">noisy_min_fires</span>
            <input
              type="number" min="0"
              v-model.number="thresholdsForm.noisy_min_fires"
              class="mt-1 text-sm border border-gray-300 rounded-md px-2.5 py-1.5 w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
            />
          </label>
          <label class="block">
            <span class="text-xs text-gray-600">dead_min_checks</span>
            <input
              type="number" min="1"
              v-model.number="thresholdsForm.dead_min_checks"
              class="mt-1 text-sm border border-gray-300 rounded-md px-2.5 py-1.5 w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
            />
          </label>
          <label class="block">
            <span class="text-xs text-gray-600">default_range</span>
            <select
              v-model="thresholdsForm.default_range"
              class="mt-1 text-sm border border-gray-300 rounded-md px-2.5 py-1.5 w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
            >
              <option value="24h">24h</option>
              <option value="7d">7d</option>
              <option value="30d">30d</option>
              <option value="all">all</option>
            </select>
          </label>
        </div>

        <div class="flex items-center gap-3">
          <button type="button"
            class="btn btn-primary focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-1"
            :disabled="saving || !isAdmin"
            @click="$emit('save-thresholds')">
            {{ saving ? 'Saving…' : 'Save thresholds' }}
          </button>
          <span v-if="!isAdmin" class="text-xs text-amber-700">Admin role required to change thresholds.</span>
          <Badge v-if="preview" color="blue">
            preview: {{ preview.noisy }} noisy · {{ preview.dead }} dead
          </Badge>
        </div>
      </div>
    </Card>
  </div>

  <div class="sv-group mt-6">
    <div class="sv-group-label">Trigger log retention</div>
    <p class="sv-group-meta">Trigger events accumulate over time. Wiping is reversible only by re-running rules; do it sparingly.</p>
    <Card>
      <div v-if="!stats" class="text-sm text-gray-500">Loading…</div>
      <div v-else class="space-y-3">
        <dl class="grid grid-cols-3 gap-3 text-sm">
          <div>
            <dt class="text-xs text-gray-500">Total events</dt>
            <dd class="font-mono text-base text-gray-900">{{ stats.total.toLocaleString() }}</dd>
          </div>
          <div>
            <dt class="text-xs text-gray-500">Distinct rules</dt>
            <dd class="font-mono text-base text-gray-900">{{ stats.distinct_rules }}</dd>
          </div>
          <div>
            <dt class="text-xs text-gray-500">Oldest row</dt>
            <dd class="font-mono text-xs text-gray-700">{{ stats.oldest_at || '—' }}</dd>
          </div>
        </dl>
        <div class="flex items-center gap-3 pt-1 flex-wrap">
          <label class="inline-flex items-center gap-2 text-sm">
            <span class="text-xs text-gray-500">Policy</span>
            <select :value="resetPolicy"
              @change="$emit('update:resetPolicy', Number($event.target.value))"
              class="border border-gray-300 rounded px-2 py-1 text-sm bg-white focus-visible:outline-2 focus-visible:outline-blue-500"
              :disabled="resetting || !isAdmin">
              <option v-for="o in resetOptions" :key="o.value" :value="o.value">
                {{ o.label }}
              </option>
            </select>
          </label>
          <span class="text-xs text-gray-500 font-mono">
            → {{ resetCount.toLocaleString() }} row(s)
          </span>
          <button type="button"
            class="btn btn-danger focus-visible:ring-2 focus-visible:ring-red-500 focus-visible:ring-offset-1"
            :disabled="resetting || !isAdmin || resetCount === 0"
            @click="$emit('reset-log')">
            {{ resetting ? 'Resetting…' : `Reset (${resetLabel})` }}
          </button>
          <span v-if="!isAdmin" class="text-xs text-amber-700">Admin role required.</span>
        </div>
      </div>
    </Card>
  </div>
</template>
