<script setup>
// A folded turn's useful tail, shown under its own chips.
//
// Folding a turn otherwise costs the reader the one thing they wanted kept —
// what it produced — and the chips only say a shell span existed, not what it
// printed. Rendered, not summarised, for exactly that reason.
//
// It used to sit above the NEXT turn's prompt, as the thing that prompt is
// answering. That placement cannot survive a live session: merge keeps a
// PENDING prompt placeholder beside the turn being written, so the tail was
// handed to a group that renders no card.
//
// It is a REFERENCE, never a copy: the span is not added to this turn's event
// list, so counts, exports and keyboard walks still see it once. Clicking
// selects the original id in the detail rail.
import { computed } from 'vue'
import ConversationSpanCard from './ConversationSpanCard.vue'

const props = defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
  folding: { type: Object, required: true },
  agentMerge: { type: Object, default: null },
  workflowRunsById: { type: Object, default: () => ({}) },
})
defineEmits(['activate', 'enter-scope'])

// Spend belongs to the turn that incurred it. A carried card that repeated the
// token counts made the same tokens look billed twice to anyone scanning the
// feed; durations stay, because how long it took is a fact about the span.
const TOKEN_KEYS = ['tokens', 'main_session_impact_tokens', 'subagent_tokens']
const stripped = computed(() => {
  const span = props.span
  const attributes = { ...(span.attributes || {}) }
  for (const k of TOKEN_KEYS) delete attributes[k]
  return { ...span, output_tokens: null, attributes }
})
</script>

<template>
  <!-- The stem spans the 26px gap exactly, so the card reads as the tail of
       the block above rather than as a floating card. SOLID: solid spines are
       spans of this turn, and this one is — dashed used to mean the card had
       been carried across a turn boundary, which it no longer is. -->
  <div class="carried" data-testid="carried-span">
    <span class="carried-stem" aria-hidden="true"></span>
    <!-- The ORIGINAL id, not a copy of the span under a new one: the feed's
         delegated double-click-to-read resolves the row it is on by this
         attribute, and a carried card must resolve to the span it references. -->
    <div class="carried-body" :data-span-id="span.span_id">
      <ConversationSpanCard
        :span="stripped"
        :selected-span="selectedSpan"
        :folding="folding"
        :agent-merge="agentMerge"
        :workflow-runs-by-id="workflowRunsById"
        @activate="$emit('activate', span)"
        @enter-scope="$emit('enter-scope', $event)"
      />
    </div>
  </div>
</template>

<style scoped>
.carried {
  margin-bottom: 10px;
}
.carried-stem {
  display: block;
  width: 0;
  height: 24px;
  margin: -24px 0 0 25px;
  border-left: 2px solid var(--color-border-strong);
}
.carried-body {
  border: 1px solid var(--color-border-subtle);
  border-top-color: var(--color-border-strong);
  border-radius: 12px;
  background: var(--color-surface-2);
  padding: 10px 12px 10px 0;
}
/* Puts the stem and the inner spine on one vertical line: the body's own 1px
   border plus this 24px margin lands the rule at x + 25px, exactly where the
   stem drops it. */
.carried-body > :deep(*) {
  margin-left: 24px;
  padding-left: 15px;
  border-left: 2px solid var(--color-border-strong);
}
</style>
