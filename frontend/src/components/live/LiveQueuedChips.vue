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
import { computed } from 'vue'
import { stripMarkdown } from '../../utils/liveRows.js'

const props = defineProps({
  items: { type: Array, default: () => [] },
})

const rows = computed(() => props.items.map((q, i) => {
  const steer = q.source === 'bridge' || q.optimistic
  return {
    key: `${i}-${q.content?.slice(0, 24) || ''}`,
    steer,
    label: steer ? '⧗ steering…' : '⧗ queued',
    text: stripMarkdown(q.content || ''),
  }
}))
</script>

<template>
  <div v-if="rows.length" class="live-queued" data-testid="live-queued">
    <div
      v-for="r in rows"
      :key="r.key"
      class="live-row-msg live-queued-item"
      :class="{ 'live-queued-steer': r.steer }"
      data-testid="live-queued-item"
      :title="r.text"
    >
      <span class="live-msg-eyebrow">{{ r.label }}</span>
      <div class="live-msg-body">{{ r.text }}</div>
    </div>
  </div>
</template>
