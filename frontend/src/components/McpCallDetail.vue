<script setup>
// The params/result round-trip for an MCP tool-call span, extracted from
// SpanDetailPanel so its v-if branches don't push that already-dense SFC over
// the vue-complexity template threshold. `call` is the shape SpanDetailPanel's
// `mcpCall` computed builds: { input, inputDropped, result, resultDropped }.
defineProps({
  call: { type: Object, required: true },
})
</script>

<template>
  <div class="mb-4 space-y-3">
    <div v-if="call.input != null">
      <div class="flex items-center justify-between mb-1">
        <div class="text-xs text-gray-400">Params</div>
        <span
          v-if="call.inputDropped"
          class="text-[10px] text-amber-700 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded"
          :title="`${call.inputDropped} bytes truncated`"
        >truncated</span>
      </div>
      <code class="text-xs bg-gray-50 px-1.5 py-1 rounded block whitespace-pre-wrap break-words max-h-60 overflow-y-auto font-mono">{{ call.input }}</code>
    </div>
    <div v-if="call.result != null">
      <div class="flex items-center justify-between mb-1">
        <div class="text-xs text-gray-400">Result</div>
        <span
          v-if="call.resultDropped"
          class="text-[10px] text-amber-700 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded"
          :title="`${call.resultDropped} bytes truncated`"
        >truncated</span>
      </div>
      <code class="text-xs bg-gray-50 px-1.5 py-1 rounded block whitespace-pre-wrap break-words max-h-96 overflow-y-auto font-mono">{{ call.result }}</code>
    </div>
  </div>
</template>
