<script setup>
import { computed, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Checkbox from '../ui/Checkbox.vue'

const props = defineProps({
  memories: { type: Array, default: () => [] },
  selectedId: { type: String, default: null },
  busy: { type: Boolean, default: false },
  expanded: { type: Boolean, default: false },
})
const emit = defineEmits(['select', 'bulk'])

const checked = ref(new Set())

watch(() => props.memories, () => { checked.value = new Set() })

const checkedIds = computed(() => [...checked.value])
const allChecked = computed(() =>
  props.memories.length > 0 && checked.value.size === props.memories.length)

const KIND_STYLES = {
  lesson: 'bg-violet-100 text-violet-700',
  gotcha: 'bg-amber-100 text-amber-800',
  preference: 'bg-blue-100 text-blue-700',
  fact: 'bg-slate-100 text-slate-600',
  procedure: 'bg-cyan-100 text-cyan-700',
}

// Solid hue per kind, painted as a thin stripe down the card's left edge so the
// grid reads at a glance — color, not text, carries the first signal.
const KIND_BAR = {
  lesson: 'bg-violet-400',
  gotcha: 'bg-amber-400',
  preference: 'bg-blue-400',
  fact: 'bg-slate-300',
  procedure: 'bg-cyan-400',
}

function kindCls(kind) {
  return KIND_STYLES[kind] || KIND_STYLES.fact
}

function kindBar(kind) {
  return KIND_BAR[kind] || KIND_BAR.fact
}

function timeLabel(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleString([], {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function snippet(body) {
  const text = (body || '').replace(/[#*`>\-]/g, '').replace(/\s+/g, ' ').trim()
  return text.length > 110 ? `${text.slice(0, 110)}…` : text
}

function toggle(id) {
  const next = new Set(checked.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  checked.value = next
}

function toggleAll() {
  checked.value = allChecked.value
    ? new Set()
    : new Set(props.memories.map(m => m.id))
}

function runBulk(action) {
  if (!checked.value.size) return
  emit('bulk', { action, ids: checkedIds.value })
}
</script>

<template>
  <div class="flex flex-col min-w-0">
    <!-- Bulk-action bar floats over the viewport bottom while a selection is
         active, so it never scrolls behind the sticky header or over the cards.
         Teleported to <body> to escape any transformed/clipping ancestor. -->
    <Teleport to="body">
      <div
        v-if="checked.size"
        class="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 flex items-center gap-4 px-4 py-2.5 rounded-full bg-white shadow-lg ring-1 ring-slate-200 text-sm"
      >
        <span class="font-medium text-blue-800">{{ checked.size }} selected</span>
        <span class="flex items-center gap-4">
          <Button variant="link" size="sm" class="font-medium text-emerald-700 hover:text-emerald-900 hover:no-underline" @click="runBulk('approve')">Approve</Button>
          <Button variant="link" size="sm" class="font-medium text-slate-600 hover:text-slate-900 hover:no-underline" @click="runBulk('retire')">Retire</Button>
          <Button variant="link" size="sm" class="font-medium text-emerald-700 hover:text-emerald-900 hover:no-underline" @click="runBulk('restore')">Restore</Button>
          <Button variant="link" size="sm" class="font-medium text-red-600 hover:text-red-800 hover:no-underline" @click="runBulk('forget')">Forget</Button>
          <Button variant="link" size="sm" class="text-slate-400 hover:text-slate-700 hover:no-underline" @click="checked = new Set()">Clear</Button>
        </span>
      </div>
    </Teleport>

    <div v-if="memories.length" class="flex items-center gap-2 px-1 mb-1">
      <Checkbox
        :model-value="allChecked"
        aria-label="Select all memories"
        @update:model-value="toggleAll"
      />
      <span class="text-[11px] text-slate-400">{{ memories.length }} shown</span>
    </div>

    <div v-if="busy && !memories.length" class="text-slate-500 text-sm py-16 text-center">
      Loading…
    </div>
    <div v-else-if="!memories.length" class="text-slate-500 text-sm py-16 text-center">
      Nothing here.
    </div>
    <ul
      v-else
      class="overflow-y-auto"
      :class="expanded ? 'grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-2' : 'space-y-1.5'"
    >
      <li
        v-for="m in memories"
        :key="m.id"
        class="relative flex gap-2 rounded-md border pl-3.5 pr-2.5 py-1.5 overflow-hidden transition-all"
        :class="[
          selectedId === m.id ? 'border-blue-300 bg-blue-50/60' : 'border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm',
          expanded ? 'items-start' : 'items-center',
        ]"
      >
        <span class="absolute inset-y-0 left-0 w-1" :class="kindBar(m.kind)" aria-hidden="true"></span>
        <Checkbox
          :model-value="checked.has(m.id)"
          :class="expanded ? 'mt-1' : ''"
          :aria-label="`Select ${m.title || m.kind}`"
          @update:model-value="toggle(m.id)"
        />
        <button
          type="button"
          class="flex-1 min-w-0 text-left focus-visible:outline-2 focus-visible:outline-blue-500 rounded"
          @click="emit('select', m.id)"
        >
          <div class="flex items-center gap-2">
            <span
              class="text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded shrink-0"
              :class="kindCls(m.kind)"
            >{{ m.kind }}</span>
            <span
              v-if="m.status !== 'active'"
              class="text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 shrink-0"
            >{{ m.status }}</span>
            <span
              class="flex-1 min-w-0 truncate text-sm"
              :class="m.title ? 'font-medium text-slate-800' : 'text-slate-500'"
            >{{ m.title || snippet(m.body) }}</span>
            <span class="text-[10px] font-mono text-slate-400 shrink-0">{{ timeLabel(m.updated_at) }}</span>
          </div>
          <p v-if="expanded && m.title" class="mt-1 text-xs text-slate-500 leading-snug">{{ snippet(m.body) }}</p>
        </button>
      </li>
    </ul>
  </div>
</template>
