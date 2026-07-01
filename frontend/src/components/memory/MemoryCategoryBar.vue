<script setup>
import { computed } from 'vue'
import Select from '../ui/Select.vue'

const props = defineProps({
  stats: { type: Object, default: () => ({}) },
  active: { type: String, default: 'all' },
  scope: { type: String, default: '' },
})
const emit = defineEmits(['select', 'update:scope'])

const byStatus = computed(() => props.stats?.by_status || {})
// Tier/kind chips filter the list to status='active', so their badges read the
// active-only buckets — otherwise a tier whose rows are all retired/proposed
// shows a count but clicks through to an empty list. (Doctor uses the
// full-status by_tier/by_kind for its corpus census.)
const byKind = computed(() => props.stats?.by_kind_active || {})
const byTier = computed(() => props.stats?.by_tier_active || {})
const byScope = computed(() => props.stats?.by_scope || {})

const KIND_ITEMS = [
  { key: 'lesson', label: 'Lessons' },
  { key: 'gotcha', label: 'Gotchas' },
  { key: 'fact', label: 'Facts' },
  { key: 'procedure', label: 'Procedures' },
  { key: 'preference', label: 'Preferences' },
]

const groups = computed(() => [
  [{ key: 'inbox', label: 'Inbox', count: byStatus.value.proposed || 0, accent: true }],
  [
    { key: 'all', label: 'All active', count: byStatus.value.active || 0 },
    ...KIND_ITEMS
      .filter(k => byKind.value[k.key])
      .map(k => ({ key: `kind:${k.key}`, label: k.label, count: byKind.value[k.key] })),
  ],
  [
    { key: 'tier:working', label: 'Working', count: byTier.value.working || 0 },
    { key: 'tier:episodic', label: 'Episodic', count: byTier.value.episodic || 0 },
  ],
  [{ key: 'retired', label: 'Retired', count: byStatus.value.retired || 0 }],
])

const scopeOptions = computed(() => Object.keys(byScope.value).sort())
const scopeSelectOptions = computed(() => [
  { value: '', label: 'All scopes' },
  ...scopeOptions.value.map((s) => ({ value: s, label: `${s} (${byScope.value[s]})` })),
])
</script>

<template>
  <div class="flex flex-wrap items-center gap-1.5" role="group" aria-label="Memory categories">
    <template v-for="(group, gi) in groups" :key="gi">
      <span v-if="gi > 0" class="w-px h-4 bg-slate-200 mx-1 hidden sm:block" aria-hidden="true"></span>
      <button
        v-for="item in group"
        :key="item.key"
        type="button"
        class="inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full border transition-colors focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="active === item.key
          ? 'bg-blue-50 border-blue-300 text-blue-800'
          : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300'"
        :aria-current="active === item.key ? 'true' : undefined"
        @click="emit('select', item.key)"
      >
        {{ item.label }}
        <span
          class="text-[10px] font-mono px-1 rounded-full"
          :class="item.accent && item.count
            ? 'bg-amber-100 text-amber-800 font-semibold'
            : 'bg-slate-100 text-slate-500'"
        >{{ item.count }}</span>
      </button>
    </template>

    <div v-if="scopeOptions.length > 1" class="w-44 ml-auto">
      <Select
        :model-value="scope"
        :options="scopeSelectOptions"
        block
        class="text-xs"
        aria-label="Scope filter"
        @update:model-value="emit('update:scope', $event)"
      />
    </div>
  </div>
</template>
