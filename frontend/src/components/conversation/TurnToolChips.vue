<script setup>
import { computed } from 'vue'
import { toolDisplayName } from '../../utils/traceFormatters.js'

// The collapsed turn's preview strip: its descendant spans grouped into
// semantic chips, so the reader sees `Read×2  Edit×4  doc-hygiene` instead of
// an unstructured wall.
const props = defineProps({
  // The turn's flattened descendants, as `{ span, ... }` entries.
  descendants: { type: Array, default: () => [] },
})
defineEmits(['expand'])

const chips = computed(() => {
  const buckets = new Map() // label -> { count, color }
  function add(label, color) {
    const cur = buckets.get(label) || { count: 0, color }
    cur.count++
    buckets.set(label, cur)
  }
  for (const { span } of props.descendants) {
    const n = span.name || ''
    if (n === 'tool.Bash') {
      add('Shell', 'bg-slate-100 text-slate-700 border-slate-200')
    } else if (n.startsWith('tool.')) {
      add(toolDisplayName(n.slice(5)), 'bg-blue-50 text-blue-700 border-blue-200')
    } else if (n === 'skill.read' || n === 'skill.invoke') {
      add(span.attributes?.skill_id || 'skill', 'bg-green-50 text-green-700 border-green-200')
    } else if (n === 'file.edit' || n === 'plan.edit') {
      add('Edit', 'bg-orange-50 text-orange-700 border-orange-200')
    } else if (n === 'rule.check') {
      add('rule', 'bg-red-50 text-red-700 border-red-200')
    } else if (n === 'memory.recall') {
      add('recall', 'bg-fuchsia-50 text-fuchsia-700 border-fuchsia-200')
    } else if (n === 'assistant_response') {
      add('assistant', 'bg-green-50 text-emerald-700 border-green-200')
    } else if (n.startsWith('subagent.')) {
      add('subagent', 'bg-violet-50 text-violet-700 border-violet-200')
    }
  }
  return Array.from(buckets.entries()).map(([label, v]) => ({ label, ...v }))
})
</script>

<template>
  <div data-testid="turn-tool-chips" class="flex flex-wrap items-center gap-1.5 pt-[3px] pl-[42px]">
    <span
      v-for="chip in chips"
      :key="chip.label"
      class="inline-flex items-center gap-1 px-[9px] py-[3px] text-[11px] rounded-[7px] border"
      :class="chip.color"
    >{{ chip.label }}<span v-if="chip.count > 1" class="opacity-65">×{{ chip.count }}</span></span>
    <button
      type="button"
      class="font-mono text-[11px] text-slate-400 hover:text-slate-700 ml-1 cursor-pointer rounded px-1 focus-visible:outline-2 focus-visible:outline-blue-500"
      @click="$emit('expand')"
    >show detail ▾</button>
  </div>
</template>
