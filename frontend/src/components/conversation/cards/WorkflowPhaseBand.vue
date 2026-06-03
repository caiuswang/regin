<script setup>
// Workflow phase band (dynamic-workflow runs only). `agentCount` is resolved
// by the parent via useAgentLaunchMerge (childrenOf the phase span).
defineProps({
  span: { type: Object, required: true },
  agentCount: { type: Number, default: 0 },
})
</script>

<template>
  <div class="flex items-center gap-2 my-3 select-none">
    <div class="flex-1 border-t border-dashed border-emerald-300"></div>
    <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-emerald-300 bg-emerald-50 text-[10px] font-semibold uppercase tracking-wider text-emerald-700">
      <span>Phase {{ span.attributes?.index ?? '' }}</span>
      <template v-if="span.attributes?.title"><span class="text-emerald-400">·</span><span class="normal-case">{{ span.attributes.title }}</span></template>
      <span class="text-emerald-400">·</span>
      <span class="normal-case">{{ agentCount }} agent<span v-if="agentCount !== 1">s</span></span>
    </span>
    <div class="flex-1 border-t border-dashed border-emerald-300"></div>
  </div>
</template>
