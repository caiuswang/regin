<script setup>
// The conversation feed's reading-density control: one segmented track, three
// exclusive modes. Its own component rather than three more buttons inline in
// SessionTraceHeader — that template sits a handful of bindings under the
// hard 100-binding threshold, and a five-binding-per-item loop would blow it.
//
// A segmented track, not three toggles: Condensed and Focus answer the same
// question at different strengths, and composing them produces a spine nobody
// can predict.
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import {
  useFocusMode, DETAIL_FULL, DETAIL_CONDENSED, DETAIL_LATEST,
} from '../../composables/useFocusMode.js'

const { detail, setDetail } = useFocusMode()

// `testid` keeps the shipped `focus-toggle` hook on the segment that does what
// the old boolean button did, so the specs that drive tier folding still have
// a stable handle.
const MODES = [
  {
    id: DETAIL_FULL,
    label: 'Full',
    icon: 'detail-full',
    testid: 'detail-full',
    title: 'Every span of an expanded turn, full bodies',
  },
  {
    id: DETAIL_CONDENSED,
    label: 'Condensed',
    icon: 'detail-condensed',
    testid: 'focus-toggle',
    title: 'Low-signal spans fold into one strip per turn. Nothing is filtered. (C)',
  },
  {
    id: DETAIL_LATEST,
    label: 'Focus',
    icon: 'detail-latest',
    testid: 'latest-toggle',
    title: 'One card per turn — its newest useful span, body intact. Nothing is filtered. (F)',
  },
]
</script>

<template>
  <div
    class="inline-flex shrink-0 items-center gap-0.5 rounded-[10px] bg-surface-2 p-[3px]"
    role="group"
    aria-label="Detail"
    data-testid="detail-control"
  >
    <Button
      v-for="m in MODES"
      :key="m.id"
      variant="ghost"
      size="sm"
      :data-testid="m.testid"
      class="h-auto gap-1.5 rounded-lg px-2.5 py-[5px] text-[11.5px]"
      :class="detail === m.id
        ? 'bg-surface text-fg font-bold shadow-[0_1px_2px_rgb(15_23_42_/_10%)] hover:bg-surface'
        : 'font-medium text-fg-subtle'"
      :aria-pressed="detail === m.id"
      :title="m.title"
      @click="setDetail(m.id)"
    >
      <Icon :name="m.icon" :size="14" :class="detail === m.id ? '' : 'opacity-50'" />
      <span class="hidden sm:inline">{{ m.label }}</span>
    </Button>
  </div>
</template>
