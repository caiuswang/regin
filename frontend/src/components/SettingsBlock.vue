<script setup>
import { computed } from 'vue'
import Card from './Card.vue'
import ToggleSwitch from './ToggleSwitch.vue'
import Button from './ui/Button.vue'
import Select from './ui/Select.vue'

// Generic editor for one nested settings block (agent_memory, agent_messages,
// …). `fields` carries per-field metadata from /api/settings/<block>; `form`
// is the edited {key: value} copy. Renders one card per `group`, each a
// Setting | Value table — the same table-in-card pattern the Configuration
// and Hook Handlers tabs use.
const props = defineProps({
  title: { type: String, default: 'Settings' },
  description: { type: String, default: '' },
  fields: { type: Array, default: () => [] },
  form: { type: Object, default: null },
  saving: { type: Boolean, default: false },
})

defineEmits(['save'])

// One card per `group`, preserving first-seen order of the field list.
const groups = computed(() => {
  const out = []
  for (const f of props.fields) {
    let g = out.find(x => x.name === f.group)
    if (!g) { g = { name: f.group, fields: [] }; out.push(g) }
    g.fields.push(f)
  }
  return out
})
</script>

<template>
  <div class="sv-section-header">
    <h2 class="sv-section-title">{{ title }}</h2>
    <p class="sv-section-desc">{{ description }}</p>
  </div>

  <div v-if="!form" class="text-sm text-gray-500">Loading…</div>
  <template v-else>
    <div
      v-for="(g, i) in groups"
      :key="g.name"
      class="sv-group"
      :class="{ 'mt-6': i > 0 }"
    >
      <div class="sv-group-label">{{ g.name }}</div>
      <Card :no-padding="true">
        <table class="tbl">
          <thead>
            <tr><th>Setting</th><th class="text-right">Value</th></tr>
          </thead>
          <tbody>
            <tr v-for="f in g.fields" :key="f.key">
              <td>
                <div class="font-medium text-gray-900">{{ f.label }}</div>
                <div class="text-xs text-gray-500 mt-0.5 leading-snug">{{ f.description }}</div>
                <div class="text-xs text-gray-400 mt-1">
                  <code>{{ f.key }}</code> · default <code>{{ String(f.default) }}</code>
                </div>
              </td>
              <td class="text-right align-top whitespace-nowrap">
                <ToggleSwitch
                  v-if="f.type === 'bool'"
                  v-model="form[f.key]"
                  :aria-label="f.key"
                />
                <Select
                  v-else-if="f.type === 'choice'"
                  v-model="form[f.key]"
                  :aria-label="f.key"
                  :options="f.options"
                />
                <input
                  v-else-if="f.type === 'string'"
                  type="text"
                  v-model="form[f.key]"
                  :aria-label="f.key"
                  class="text-sm border border-gray-300 rounded-md px-2.5 py-1.5 w-56 max-w-full text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                />
                <input
                  v-else
                  type="number"
                  :min="f.min"
                  :max="f.max"
                  :step="f.step"
                  v-model.number="form[f.key]"
                  :aria-label="f.key"
                  class="text-sm border border-gray-300 rounded-md px-2.5 py-1.5 w-24 text-right focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                />
              </td>
            </tr>
          </tbody>
        </table>
      </Card>
    </div>

    <div class="mt-5">
      <Button
        variant="primary"
        :disabled="saving"
        @click="$emit('save')"
      >
        {{ saving ? 'Saving…' : 'Save settings' }}
      </Button>
    </div>
  </template>
</template>
