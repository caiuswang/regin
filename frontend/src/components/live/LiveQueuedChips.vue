<script setup>
// Queued / steering prompts for the /live card — a SERVER-authoritative,
// FIFO, queued-only list: messages typed (or bridged) while the agent is
// busy fire no hook, so they can't ride the tail as spans; the server
// serves them oldest first (transcript-derived queue, then still-pending
// bridge steers tagged `source:'bridge'`). Every row is the durable truth —
// it appears when the server represents the send and leaves only on an
// observed event (turn started, transcript consumed it, session ended,
// operator removed it); there is no client echo and no timer. Rendered as a
// vertical conversation-style list (mirrors the tail's message rows) — a
// consumed prompt is already in the conversation, so it just stops being
// served and drops off the next poll; there is no "sent" history here.
//
// Controls follow what the server can actually do with the row, keyed off
// its `id`. An SDK-tier queue row (regin's own in-memory list) is editable
// and removable; a bridge steer's `b<row>` id can only dismiss its chip —
// the keystrokes were already typed into the pane, so offering edit would be
// a control that silently changes nothing. A transcript-derived row has no
// id and no write path at all: it belongs to Claude Code.
// The parent owns both round trips — a refusal has to keep the row.
import LiveQueuedItem from './LiveQueuedItem.vue'

defineProps({
  items: { type: Array, default: () => [] },
  // Ids with a request in flight — the row goes inert rather than letting a
  // double-tap fire a second edit or remove against the same entry.
  busyIds: { type: Object, default: () => new Set() },
})
const emit = defineEmits(['edit', 'remove'])
</script>

<template>
  <div v-if="items.length" class="live-queued" data-testid="live-queued">
    <LiveQueuedItem
      v-for="(q, i) in items"
      :key="q.id || `${i}-${(q.content || '').slice(0, 24)}`"
      :item="q"
      :editable="!!q.id && q.source !== 'bridge'"
      :removable="!!q.id"
      :busy="busyIds.has(q.id)"
      @edit="e => emit('edit', e)"
      @remove="e => emit('remove', e)"
    />
  </div>
</template>
