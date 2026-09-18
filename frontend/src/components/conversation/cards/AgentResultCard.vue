<script setup>
import { computed } from 'vue'
import { AGENT_RESULT_PREVIEW_CHARS } from '../../../composables/useAgentLaunchMerge.js'
import { prettyJson, jsonFence } from '../../../utils/jsonBody.js'
import MarkdownContent from '../../MarkdownContent.vue'
import Button from '../../ui/Button.vue'

// Deferred agent RESULT card (workflow runs): the span-tree projection emits
// this AFTER the agent's turns, so each agent reads prompt → work → result.
// `span` is a synthetic marker carrying the agent's attributes.
const props = defineProps({
  span: { type: Object, required: true },
  // useAgentLaunchMerge: { agentResultText }
  agentMerge: { type: Object, required: true },
  // useConversationFolding: { isAgentResultExpanded, toggleAgentResult }
  folding: { type: Object, required: true },
})

const resultText = computed(() => String(props.agentMerge.agentResultText(props.span) ?? ''))
const expanded = computed(() => props.folding.isAgentResultExpanded(props.span.span_id))

const jsonText = computed(() => prettyJson(resultText.value))

const renderedMarkdown = computed(() =>
  (jsonText.value ? jsonFence(jsonText.value) : resultText.value))

// The clamp cuts by HEIGHT, so the affordance has to be gated on what is
// actually rendered. Pretty-printing turns a 66-char JSON result into eleven
// lines: gating that on the raw char count clips it with no way to expand.
// 4 = what .agent-result-clamp (7.4em) fits of an 11.5px <pre> with padding.
const CLAMPED_CODE_LINES = 4
// The char clause is not redundant with the line clause: `pre-wrap` reflows a
// single 600-char value into ~9 visual rows, so a 3-line result can still clip.
const clipped = computed(() => (jsonText.value
  ? jsonText.value.split('\n').length > CLAMPED_CODE_LINES
    || jsonText.value.length > AGENT_RESULT_PREVIEW_CHARS
  : resultText.value.length > AGENT_RESULT_PREVIEW_CHARS))
</script>

<template>
  <div
    class="mt-1 mb-2 rounded-md border border-emerald-200 bg-emerald-50/50 px-3 py-2"
    data-testid="agent-result"
  >
    <div class="flex items-center justify-between gap-2 mb-1">
      <span class="text-[10px] font-semibold uppercase tracking-wider text-emerald-700">result</span>
      <Button
        v-if="clipped"
        variant="link"
        size="sm"
        class="min-w-0 max-w-full text-[11px] font-medium text-emerald-700 hover:text-emerald-900"
        @click.stop="folding.toggleAgentResult(span.span_id)"
      ><span class="truncate">{{ expanded ? 'Collapse' : `Show full · ${resultText.length} chars` }}</span></Button>
    </div>
    <!-- An agent's result is markdown, and a char-sliced preview showed it as
         source ("## Verdict", "**1."). Render it, and collapse by height behind
         a fade so the cut lands between rendered blocks, never mid-token. -->
    <div
      class="agent-result-md text-[12.5px] text-slate-700 break-words leading-relaxed"
      :class="[expanded ? 'max-h-[32rem] overflow-y-auto' : 'agent-result-clamp', jsonText ? 'agent-result-json' : '']"
      :data-result-format="jsonText ? 'json' : 'markdown'"
    ><MarkdownContent :markdown="renderedMarkdown" /></div>
  </div>
</template>

<style scoped>
.agent-result-clamp {
  max-height: 7.4em;
  overflow: hidden;
  mask-image: linear-gradient(180deg, #000 72%, transparent);
  -webkit-mask-image: linear-gradient(180deg, #000 72%, transparent);
}
/* Markdown at the card's scale. MarkdownContent carries a document type scale
   (a 20px h2 under a rule); in a 12.5px result box one heading fills the whole
   clamp and nothing of the answer shows. */
.agent-result-md :deep(h1),
.agent-result-md :deep(h2),
.agent-result-md :deep(h3),
.agent-result-md :deep(h4) {
  font-size: 13px;
  font-weight: 700;
  margin: 0.7em 0 0.25em;
  border: 0;
  padding: 0;
}
.agent-result-md :deep(p),
.agent-result-md :deep(ul),
.agent-result-md :deep(ol),
.agent-result-md :deep(table) { margin-bottom: 0.5em; }
.agent-result-md :deep(pre) {
  margin-bottom: 0.5em;
  padding: 0.5rem 0.625rem;
  font-size: 11.5px;
}
.agent-result-md :deep(.pattern-content > :first-child) { margin-top: 0; }
/* A pretty-printed JSON value line runs to ~600 chars; an unwrapped <pre> would
   scroll the card sideways. Prose results keep the default <pre> behaviour. */
.agent-result-json :deep(pre) {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
