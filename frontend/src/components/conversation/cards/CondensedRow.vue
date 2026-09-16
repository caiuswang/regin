<script setup>
import { computed } from 'vue'
import {
  denyBadgeLabel, fmtDuration, mcpParts, rowParts, toolRowTextClass,
} from '../../../utils/traceFormatters.js'
import { condensedBody } from '../dispatch.js'
import { plainText } from '../../../utils/plainText.js'

// Tier 2 under Focus: one 26px line carrying the semantic verb, the subject,
// and the duration — same family as InlineToolRow, but with a body it can
// open in place. Sixty of these stack in a single turn, so the row has no
// border and no shadow at rest: any per-row chrome multiplies by sixty and
// stripes the feed.
const props = defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
  expanded: { type: Boolean, default: false },
  // A folded (Tier 3) span the reader revealed via the strip. It stays a
  // one-liner and only selects — Focus-off is what full cards are for.
  dim: { type: Boolean, default: false },
})
const emit = defineEmits(['activate', 'toggle'])

const parts = computed(() => rowParts(props.span))
// Spans whose full card carried the subject (Bash's command, ToolSearch's
// query) have none in `rowParts` — the card was the only thing that ever
// showed it. Condensed, the row IS the card, so it has to fill that in or the
// reader gets a bare verb.
const subject = computed(() => {
  const a = props.span.attributes || {}
  const raw = parts.value.subject
    || a.command_preview || a.command || a.query || a.pattern || a.file_path
    // Last resort, and the one that matters for a REVEALED Tier 3 row: it
    // carries no body (below) and several folded kinds project no subject, so
    // a strip of 40 thinking spans expanded into 40 byte-identical "Thinking"
    // lines — the reader opened the fold and learned nothing.
    || condensedBody(props.span)
  return plainText(raw).split('\n').find(Boolean) || ''
})
const mcp = computed(() => mcpParts(props.span.name))
const body = computed(() => (props.dim ? '' : condensedBody(props.span)))
const selected = computed(() =>
  !!(props.selectedSpan && props.selectedSpan.span_id === props.span.span_id))
const bodyId = computed(() => `condensed-body-${props.span.span_id}`)
// A path ellipsizes from the LEFT so the basename always survives; a command
// or a query keeps normal truncation because its head is the identifying part.
// A URL is NOT a path for this purpose: its identifying half is the host, and
// left-ellipsis clipped `https://docs.example.com` down to `…/v2/get`.
const subjectIsPath = computed(() => (
  /[/\\]/.test(subject.value)
  && !/\s/.test(subject.value)
  && !/^[a-z][a-z0-9+.-]*:\/\//i.test(subject.value)
))

function onClick(e) {
  // Same guard as RuleCheckRow: a click that ended a drag-selection is the
  // reader copying the line, not asking to open it.
  if (window.getSelection?.()?.toString()) return
  emit('activate', props.span)
  if (body.value) emit('toggle', props.span.span_id)
}
</script>

<template>
  <div>
    <!-- Raw <button>, not the <Button> primitive: the primitive's
         `justify-center`, `whitespace-nowrap` and `pointer-coarse:min-h-9`
         each fight this row's 26px full-width truncating line. It borrows the
         primitive's focus ring verbatim so the treatment doesn't diverge,
         which is what that rule is actually guarding. -->
    <button
      type="button"
      class="condensed-row group/row flex w-full items-baseline gap-[9px] rounded-md px-2.5 py-[5px] text-left cursor-pointer hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
      :class="[dim ? 'opacity-70' : '', selected ? 'event-selected' : '']"
      :aria-expanded="body ? expanded : undefined"
      :aria-controls="body && expanded ? bodyId : undefined"
      @click="onClick"
    >
      <span
        v-if="mcp"
        class="inline-block shrink-0 rounded bg-cyan-100 px-1 py-px text-[9px] font-semibold uppercase tracking-wider text-cyan-800"
      >MCP</span>
      <span
        class="min-w-0 shrink truncate text-[12.5px] font-semibold"
        :class="dim ? 'text-fg-faint' : toolRowTextClass(span)"
        :title="parts.title"
      >{{ parts.title }}</span>
      <span
        v-if="subject"
        class="min-w-0 flex-1 truncate font-mono text-[12.5px]"
        :class="[dim ? 'text-fg-faint' : 'text-fg-subtle', subjectIsPath ? 'condensed-subject-rtl' : '']"
        :title="subject"
      >{{ subject }}</span>
      <span v-else class="min-w-0 flex-1"></span>
      <span
        v-if="span.attributes?.denied"
        class="shrink-0 rounded border border-amber-200 bg-amber-100 px-1 font-sans text-[10px] uppercase tracking-wider text-amber-800"
      >{{ denyBadgeLabel(span) }}</span>
      <span
        v-else-if="span.attributes?.rejected"
        class="shrink-0 rounded border border-red-200 bg-red-100 px-1 font-sans text-[10px] uppercase tracking-wider text-red-800"
      >Rejected</span>
      <!-- No boxed chevron: a bordered control on every row is the striping
           this tier exists to avoid. Present only when there IS a body. -->
      <span
        v-if="body"
        aria-hidden="true"
        class="shrink-0 text-[8px] leading-none transition-opacity opacity-55 group-hover/row:opacity-100 group-focus-visible/row:opacity-100"
      >{{ expanded ? '▾' : '▸' }}</span>
      <span
        v-if="span.duration_ms"
        class="shrink-0 font-mono text-[10.5px] tabular-nums text-fg-faint"
      >{{ fmtDuration(span.duration_ms) }}</span>
    </button>
    <pre
      v-if="body && expanded"
      :id="bodyId"
      class="mt-1 mb-1 ml-2.5 max-h-[40vh] overflow-auto whitespace-pre-wrap break-words border-l-2 border-border pl-3 font-mono text-[11.5px] leading-[1.55] text-fg-muted"
    >{{ body }}</pre>
  </div>
</template>

<style scoped>
/* Ellipsis from the left, so a deep path keeps its basename. `plaintext`
   keeps the glyph order LTR while the overflow clips at the head. */
.condensed-subject-rtl {
  direction: rtl;
  text-align: left;
  unicode-bidi: plaintext;
}
</style>
