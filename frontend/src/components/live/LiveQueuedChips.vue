<script setup>
// Queued / steering prompts for the /live card — a SERVER-authoritative,
// FIFO, queued-only list: messages typed (or bridged) while the agent is
// busy fire no hook, so they can't ride the tail as spans; the server
// derives them live from the transcript (`queued_prompts`, oldest first) and
// tags a not-yet-flushed bridge steer `source:'bridge'`. An optimistic
// client entry (a just-sent steer) carries `optimistic:true` as a brief echo
// until a poll represents it server-side — it is never the durable truth,
// and a re-derived server entry survives a reload; a client one doesn't need
// to. Rendered as a vertical conversation-style list (oldest → newest,
// mirrors the tail's message rows) — a consumed prompt is already in the
// conversation, so it just stops being served and drops off the next poll;
// there is no "sent" history here.
//
// A row is editable only when the server gave it an `id`, which only the SDK
// tier's queue can: it is regin's own in-memory list. The transcript-derived
// queue of a terminal session belongs to Claude Code and has no write path, so
// those rows stay read-only rather than offering a control that does nothing.
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
      :editable="!!q.id"
      :busy="busyIds.has(q.id)"
      @edit="e => emit('edit', e)"
      @remove="e => emit('remove', e)"
    />
  </div>
</template>
