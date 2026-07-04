<script setup>
import Card from './Card.vue'
import Badge from './Badge.vue'
import Button from './ui/Button.vue'
import Select from './ui/Select.vue'

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
    <Card :no-padding="true">
      <div v-if="!thresholdsForm" class="p-5 text-sm text-gray-500">Loading…</div>
      <table v-else class="tbl">
        <thead>
          <tr><th>Setting</th><th class="text-right">Value</th></tr>
        </thead>
        <tbody>
          <tr>
            <td>
              <div class="font-medium text-gray-900">Noisy: min trigger rate</div>
              <div class="text-xs text-gray-400 mt-1"><code>noisy_min_rate_pct</code> · percent, 0–100</div>
            </td>
            <td class="text-right">
              <input type="number" min="0" max="100" aria-label="noisy_min_rate_pct"
                v-model.number="thresholdsForm.noisy_min_rate_pct"
                class="text-sm border border-gray-300 rounded-md px-2.5 py-1.5 w-24 text-right focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500" />
            </td>
          </tr>
          <tr>
            <td>
              <div class="font-medium text-gray-900">Noisy: min fire count</div>
              <div class="text-xs text-gray-400 mt-1"><code>noisy_min_fires</code></div>
            </td>
            <td class="text-right">
              <input type="number" min="0" aria-label="noisy_min_fires"
                v-model.number="thresholdsForm.noisy_min_fires"
                class="text-sm border border-gray-300 rounded-md px-2.5 py-1.5 w-24 text-right focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500" />
            </td>
          </tr>
          <tr>
            <td>
              <div class="font-medium text-gray-900">Dead: min checks with zero fires</div>
              <div class="text-xs text-gray-400 mt-1"><code>dead_min_checks</code></div>
            </td>
            <td class="text-right">
              <input type="number" min="1" aria-label="dead_min_checks"
                v-model.number="thresholdsForm.dead_min_checks"
                class="text-sm border border-gray-300 rounded-md px-2.5 py-1.5 w-24 text-right focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500" />
            </td>
          </tr>
          <tr>
            <td>
              <div class="font-medium text-gray-900">Default time range</div>
              <div class="text-xs text-gray-400 mt-1"><code>default_range</code></div>
            </td>
            <td class="text-right">
              <Select aria-label="default_range" v-model="thresholdsForm.default_range"
                :options="['24h', '7d', '30d', 'all']" />
            </td>
          </tr>
        </tbody>
      </table>
    </Card>

    <div class="mt-4 flex items-center gap-3">
      <Button variant="primary"
        :disabled="saving || !isAdmin"
        @click="$emit('save-thresholds')">
        {{ saving ? 'Saving…' : 'Save thresholds' }}
      </Button>
      <span v-if="!isAdmin" class="text-xs text-amber-700">Admin role required to change thresholds.</span>
      <Badge v-if="preview" color="blue">
        preview: {{ preview.noisy }} noisy · {{ preview.dead }} dead
      </Badge>
    </div>
  </div>

  <div class="sv-group mt-6">
    <div class="sv-group-label">Trigger log retention</div>
    <p class="sv-group-meta">Trigger events accumulate over time. Wiping is reversible only by re-running rules; do it sparingly.</p>
    <Card>
      <div v-if="!stats" class="text-sm text-gray-500">Loading…</div>
      <div v-else class="space-y-3">
        <dl class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3 text-sm">
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
            <Select :model-value="resetPolicy"
              @update:model-value="$emit('update:resetPolicy', Number($event))"
              :options="resetOptions"
              :disabled="resetting || !isAdmin" />
          </label>
          <span class="text-xs text-gray-500 font-mono">
            → {{ resetCount.toLocaleString() }} row(s)
          </span>
          <Button variant="danger"
            :disabled="resetting || !isAdmin || resetCount === 0"
            @click="$emit('reset-log')">
            {{ resetting ? 'Resetting…' : `Reset (${resetLabel})` }}
          </Button>
          <span v-if="!isAdmin" class="text-xs text-amber-700">Admin role required.</span>
        </div>
      </div>
    </Card>
  </div>
</template>
