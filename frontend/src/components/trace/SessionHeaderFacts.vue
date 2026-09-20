<script setup>
// The session's fixed identity facts, as one chip row: working directory,
// trace id, model, start clock. Fixed-length by construction — every session
// has exactly these four (minus whatever is null) — which is why they are
// separated from the pivot chips next door, whose count varies with what the
// session did. Mixing the two let one extra `plans 3` chip push `started …`
// onto a line of its own.
//
// No `·` separators: they were siblings of the items, so a wrap stranded a
// dot at the end of the line above. The shared `.hdr-chip` geometry is what
// carries "these are peers" instead.
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import TraceProjectChip from '../TraceProjectChip.vue'
import { useCopy } from '../../composables/useCopy.js'

const props = defineProps({
  session: { type: Object, required: true },
})

const { copyText, isCopied } = useCopy()
const traceId = computed(() => props.session?.trace_id || '')
const traceIdCopied = computed(() => isCopied(traceId.value))
// Copy the WHOLE id, show the head. A 36-char UUID was the single widest
// element in the header — ~230px of a string nobody reads and everybody
// copies.
const traceIdShort = computed(() => traceId.value.slice(0, 8))

// Local, not traceFormatters' `fmtTime`: that one renders HH:MM and this row
// has always shown seconds.
const startedClock = computed(() => {
  const iso = props.session?.started_at
  if (!iso) return ''
  const d = new Date(iso)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
})
</script>

<template>
  <div
    class="mt-1.5 flex flex-wrap items-center gap-x-1.5 gap-y-1"
    data-testid="trace-header-facts"
  >
    <!-- Leads the row on purpose: a terminal statusline answers "where am I"
         before anything else, and the trace id answers a question the reader
         only asks once they already know that. -->
    <TraceProjectChip :session="session" />
    <Button
      v-if="traceId"
      variant="ghost"
      size="sm"
      class="hdr-chip"
      data-testid="trace-header-trace-id"
      :title="traceIdCopied ? 'Copied trace id' : `Copy trace id: ${traceId}`"
      @click.stop="copyText(traceId)"
    >{{ traceIdShort }}<span class="opacity-50">…</span>
      <Icon
        :name="traceIdCopied ? 'check' : 'copy'"
        :size="10"
        class="shrink-0 opacity-60"
      />
    </Button>
    <span v-if="session.model" class="hdr-chip" :title="`model: ${session.model}`">
      <span class="hdr-chip-clip">{{ session.model }}</span>
    </span>
    <span
      v-if="startedClock"
      class="hdr-chip"
      :title="`session started ${session.started_at}`"
    ><span class="opacity-60">started</span>{{ startedClock }}</span>
  </div>
</template>
