<script setup>
// Queued / steering prompts for the /live card — messages typed (or bridged)
// while the agent is busy fire no hook, so they can't ride the tail as spans;
// the server derives them from the transcript (`queued_prompts`) and tags a
// not-yet-flushed bridge steer `source:'bridge'`. An optimistic client entry
// (a just-sent steer) carries `optimistic:true` until a poll returns the real
// one. A compact strip above the NOW zone; each row truncates to one line.
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
      class="live-queued-item"
      :class="{ 'live-queued-steer': r.steer }"
      data-testid="live-queued-item"
      :title="r.text"
    >
      <span class="live-queued-tag">{{ r.label }}</span>
      <span class="live-queued-text">{{ r.text }}</span>
    </div>
  </div>
</template>
