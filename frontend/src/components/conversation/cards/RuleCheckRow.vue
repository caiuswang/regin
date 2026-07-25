<script setup>
// Rule check row: status + engine·lang chips + file basename on the left,
// applicable/total count pinned right. Full per-rule list lives in the Span
// details side panel.
//
// Layout caveat: the text-bearing content is wrapped in ONE flex child (the
// inner div). Putting each span directly under the flex row would make every
// span an anonymous flex item, and `getSelection().toString()` injects a
// newline between flex items — pasting into the find bar then matches nothing.
// `{{ ' ' }}` text nodes put literal spaces in the DOM (the template compiler
// otherwise strips whitespace between sibling tags).
const props = defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
})
const emit = defineEmits(['activate'])

// Skip the row-select side effect when the click ended a drag-selection —
// otherwise the highlighted text would vanish as `selectedSpan` re-rendered.
function onRowClick() {
  if (typeof window !== 'undefined') {
    const sel = window.getSelection?.()
    if (sel && sel.toString().length > 0) return
  }
  emit('activate', props.span)
}
</script>

<template>
  <div
    tabindex="0"
    class="flex items-baseline gap-[9px] text-xs cursor-pointer rounded-lg border border-transparent px-2.5 py-[5px] hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-blue-500"
    :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'event-selected' : ''"
    @click="onRowClick"
  >
    <!-- Title takes the check's outcome color (design: a passing check reads
         green, a violating one red) — the row is scanned by color first. -->
    <span
      class="text-[12px] font-semibold shrink-0"
      :class="span.attributes?.status === 'violation' ? 'text-red-700' : 'text-emerald-800'"
    >Rule check</span>
    <div class="flex-1 min-w-0 truncate cursor-text select-text whitespace-nowrap text-[12.5px]">
      <span
        class="font-mono text-slate-500"
        :title="span.attributes?.relative_path || ''"
      >{{ span.attributes?.relative_path ? span.attributes.relative_path.split('/').pop() : '' }}</span>
      {{ ' ' }}<template v-for="(tag, ti) in (span.attributes?.engine_tags || [])" :key="ti"
        ><span
          class="hidden sm:inline font-mono text-[10px] text-slate-600 bg-slate-100 border border-slate-200 px-1 rounded"
          :title="`engine: ${tag.engine}, language: ${tag.language}`"
        >{{ tag.engine }}·{{ tag.language }}</span>{{ ' ' }}</template
      ><span
        v-if="span.attributes?.status === 'violation'"
        class="text-red-700 bg-red-100 px-[7px] py-px rounded-md text-[10px] font-bold uppercase tracking-[0.05em]"
      >⚠ {{ span.attributes.violating_rule_count }}</span
      ><span
        v-else-if="span.attributes?.status === 'no_applicable_rules'
          || span.attributes?.status === 'all_rules_out_of_scope'"
        class="text-slate-500 italic"
        :title="span.attributes?.status === 'no_applicable_rules'
          ? 'no rules applied to this file (check passed)'
          : 'all configured rules are out of scope (check passed)'"
      >ok·n/a</span
      ><span
        v-else
        class="text-emerald-700 bg-emerald-100 px-[7px] py-px rounded-md text-[10px] font-bold uppercase tracking-[0.05em]"
        title="all applicable rules passed"
      >ok</span>
    </div>
    <span
      class="font-mono text-[10.5px] text-slate-400 shrink-0 tabular-nums cursor-text select-text"
      :title="`${span.attributes?.applicable_rule_count || 0} applicable of ${span.attributes?.total_rules || 0} configured rules`"
    >{{ span.attributes?.applicable_rule_count || 0 }}/{{ span.attributes?.total_rules || 0 }}</span>
  </div>
</template>
