<script setup>
// One session in the grades list: a worst-verdict accent rail, the session
// title/trace/cost, a verdict pill per axis, and any off-frontier insight
// chip. Clicking expands the full per-axis report inline.
import { computed } from 'vue'
import GradeReportCard from '../GradeReportCard.vue'
import { toneMeta, worstTone, TONE_META } from '../../constants/gradeVerdicts'

const props = defineProps({
  // { trace_id, session, axes: { correctness, process }, point? }
  row: { type: Object, required: true },
  expanded: { type: Boolean, default: false },
})
defineEmits(['toggle'])

const axisList = computed(() => Object.entries(props.row.axes || {})
  .map(([axis, g]) => ({ axis, verdict: g.verdict, meta: toneMeta(g.verdict) })))

const railClass = computed(() =>
  TONE_META[worstTone(axisList.value.map(a => a.verdict))].bar)

// Off-frontier flags carried over from the pareto point, surfaced as the row's
// "why look here" insight. Only one ever applies (a session is either cheaply
// wrong or expensively right, never both).
const flag = computed(() => {
  const p = props.row.point
  if (p?.cheaply_wrong) {
    return { label: 'cheaply wrong', cls: 'border-rose-200 bg-rose-50 text-rose-600',
             title: 'Failed at below-median cost — the under-verification shortcut' }
  }
  if (p?.expensively_right) {
    return { label: 'expensively right', cls: 'border-indigo-200 bg-indigo-50 text-indigo-600',
             title: 'Passed, but in the top cost decile — paid for an unmanaged context' }
  }
  return null
})

const cost = computed(() => props.row.session?.cost_usd)
const title = computed(() =>
  props.row.session?.title || props.row.trace_id.slice(0, 12))
</script>

<template>
  <div class="relative overflow-hidden rounded-xl border border-slate-200 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.03)]">
    <span class="absolute inset-y-0 left-0 w-1" :class="railClass" aria-hidden="true"></span>
    <div
      class="flex cursor-pointer flex-col gap-2 py-3 pl-4 pr-3 transition-colors hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-blue-500 sm:flex-row sm:items-center sm:gap-3"
      role="button"
      tabindex="0"
      :aria-expanded="expanded"
      @click="$emit('toggle')"
      @keydown.enter="$emit('toggle')"
      @keydown.space.prevent="$emit('toggle')"
    >
      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-2">
          <router-link
            :to="`/trace/sessions/${row.trace_id}`"
            class="truncate text-sm font-medium text-slate-800 hover:text-blue-600 hover:underline"
            @click.stop
          >{{ title }}</router-link>
          <span
            v-if="flag"
            :class="['shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold', flag.cls]"
            :title="flag.title"
          >{{ flag.label }}</span>
        </div>
        <div class="mt-0.5 flex items-center gap-2 font-mono text-[11px] text-slate-400">
          <span>{{ row.trace_id.slice(0, 8) }}</span>
          <span v-if="cost">· ${{ cost.toFixed(2) }}</span>
        </div>
      </div>

      <div class="flex min-w-0 flex-wrap items-center gap-1.5 sm:shrink-0 sm:justify-end">
        <span
          v-for="a in axisList"
          :key="a.axis"
          :class="['inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium', a.meta.pill]"
        >
          <span class="opacity-70">{{ a.axis }}</span>
          <span class="font-semibold">{{ a.verdict }}</span>
        </span>
        <svg
          class="h-4 w-4 text-slate-300 transition-transform duration-200"
          :class="{ 'rotate-90': expanded }"
          viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"
        >
          <path stroke-linecap="round" stroke-linejoin="round" d="m9 5 7 7-7 7" />
        </svg>
      </div>
    </div>

    <div v-if="expanded" class="border-t border-slate-100 px-4 pb-3">
      <GradeReportCard v-for="a in axisList" :key="a.axis" :grade="row.axes[a.axis]" />
    </div>
  </div>
</template>
