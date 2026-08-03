<script setup>
// The "when" axis: rolling presets and an explicit start–end span in one
// popover. The presets cover the common case in a click; the calendar exists
// because "the Tuesday that broke CI" is not a rolling window.
import { computed, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Popover from '../ui/Popover.vue'
import {
  WEEKDAY_INITIALS, formatDay, formatSpan, monthGrid, monthLabel, shiftMonth, todayDay,
} from '../../utils/dateRange.js'

const props = defineProps({
  presets: { type: Array, required: true },
  value: { type: String, default: 'all' },
  label: { type: String, default: 'All time' },
  narrowed: { type: Boolean, default: false },
  start: { type: Object, default: null },
  end: { type: Object, default: null },
})

const emit = defineEmits(['preset', 'pick', 'clear'])

const open = ref(false)
// Re-read on every open rather than once at setup: this view is left open for
// hours, and a stale "today" would disable the new day as a future date.
const today = ref(todayDay())
const cursor = ref({ year: today.value.y, month: today.value.m })

// Reopening on a saved span should land on that span's month, not on whatever
// month the user last paged to.
watch(open, (isOpen) => {
  if (!isOpen) return
  today.value = todayDay()
  const anchor = props.start || today.value
  cursor.value = { year: anchor.y, month: anchor.m }
})

const cells = computed(() => monthGrid(cursor.value.year, cursor.value.month, {
  start: props.start, end: props.end, today: today.value,
}))
const heading = computed(() => monthLabel(cursor.value.year, cursor.value.month))
const atCurrentMonth = computed(() =>
  cursor.value.year === today.value.y && cursor.value.month === today.value.m)

const hint = computed(() => {
  if (!props.start) return 'Pick a start date'
  if (!props.end) return 'Pick an end date'
  return formatSpan(props.start, props.end)
})

function page(delta) {
  const next = shiftMonth(cursor.value.year, cursor.value.month, delta)
  cursor.value = { year: next.year, month: next.month }
}

function choosePreset(value) {
  emit('preset', value)
  open.value = false
}

function dayLabel(cell) {
  return `${formatDay(cell.day)}, ${cell.day.y}`
}

function chooseDay(cell) {
  if (cell.future) return
  // The click that CLOSES a span dismisses the picker; the one that opens it
  // must leave the grid up so the other edge is still reachable.
  const closesSpan = Boolean(props.start) && !props.end
  emit('pick', cell.day)
  if (closesSpan) open.value = false
}
</script>

<template>
  <Popover v-model:open="open" align="start">
    <template #trigger>
      <Button
        class="range-trigger"
        :class="{ 'range-trigger--on': narrowed }"
        aria-label="Filter by last activity time range"
      >
        {{ label }}
        <Icon
          name="chevron-down"
          :size="12"
          class="range-trigger__chevron"
          :class="{ 'range-trigger__chevron--open': open }"
        />
      </Button>
    </template>

    <div class="range-panel">
      <div class="range-presets" role="group" aria-label="Preset time ranges">
        <Button
          v-for="p in presets"
          :key="p.value"
          variant="ghost"
          size="sm"
          class="range-preset focus-visible:outline-2 focus-visible:outline-blue-500"
          :class="{ 'range-preset--on': value === p.value }"
          :aria-pressed="value === p.value"
          @click="choosePreset(p.value)"
        >{{ p.label }}</Button>
      </div>

      <div class="range-cal">
        <div class="range-cal__head">
          <Button
            variant="ghost"
            size="sm"
            class="range-cal__nav focus-visible:outline-2 focus-visible:outline-blue-500"
            aria-label="Previous month"
            @click="page(-1)"
          ><Icon name="chevron-left" :size="14" /></Button>
          <span class="range-cal__month">{{ heading }}</span>
          <Button
            variant="ghost"
            size="sm"
            class="range-cal__nav focus-visible:outline-2 focus-visible:outline-blue-500"
            aria-label="Next month"
            :disabled="atCurrentMonth"
            @click="page(1)"
          ><Icon name="chevron-right" :size="14" /></Button>
        </div>

        <div class="range-cal__grid" role="grid">
          <span
            v-for="(d, i) in WEEKDAY_INITIALS"
            :key="`dow-${i}`"
            class="range-cal__dow"
            aria-hidden="true"
          >{{ d }}</span>
          <Button
            v-for="cell in cells"
            :key="cell.key"
            variant="ghost"
            size="sm"
            class="range-cal__day focus-visible:outline-2 focus-visible:outline-blue-500"
            :class="{
              'range-cal__day--outside': cell.outside,
              'range-cal__day--edge': cell.edge,
              'range-cal__day--between': cell.between,
              'range-cal__day--today': cell.isToday,
            }"
            :disabled="cell.future"
            :aria-label="dayLabel(cell)"
            :aria-pressed="cell.edge || cell.between"
            @click="chooseDay(cell)"
          >{{ cell.label }}</Button>
        </div>

        <div class="range-cal__foot">
          <span class="range-cal__hint">{{ hint }}</span>
          <Button
            variant="secondary"
            size="sm"
            class="range-cal__clear focus-visible:outline-2 focus-visible:outline-blue-500"
            @click="emit('clear')"
          >Clear</Button>
        </div>
      </div>
    </div>
  </Popover>
</template>

<style scoped>
.range-trigger {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  color: var(--color-fg-muted);
  font-size: 0.78125rem;
  font-weight: 600;
  gap: 0.375rem;
  height: 2.125rem;
}
.range-trigger:hover { border-color: var(--color-border-strong); color: var(--color-fg); }
.range-trigger--on {
  background: var(--color-blue-50);
  border-color: var(--color-primary);
  color: var(--color-blue-800);
}
.range-trigger__icon { opacity: 0.7; }
.range-trigger__chevron { transition: transform 0.15s ease; }
.range-trigger__chevron--open { transform: rotate(180deg); }

/* Two panes side by side: the preset rail is the one-click path, the calendar
   the escape hatch. Stacked they'd push the grid below the fold of a popover
   anchored to a toolbar row. */
.range-panel { display: flex; flex-wrap: wrap; max-width: 100%; }
.range-presets {
  border-right: 1px solid var(--color-border-subtle);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  gap: 0.125rem;
  padding: 0.625rem;
  width: 9.125rem;
}
/* The Button primitive centres its content and pads for a standalone control;
   inside a stacked menu these need to read as a left-aligned list. */
.range-preset {
  font-size: 0.78125rem;
  font-weight: 500;
  height: 1.75rem;
  justify-content: flex-start;
  padding: 0 0.5rem;
  width: 100%;
}
.range-preset--on {
  background: var(--color-blue-50);
  color: var(--color-blue-800);
  font-weight: 700;
}

.range-cal { flex-shrink: 0; padding: 0.75rem 0.875rem 0.625rem; width: 15.125rem; }
.range-cal__head {
  align-items: center;
  display: flex;
  justify-content: space-between;
  margin-bottom: 0.375rem;
}
.range-cal__month { font-size: 0.78125rem; font-weight: 600; }
.range-cal__nav { height: 1.5rem; padding: 0; width: 1.5rem; }

.range-cal__grid {
  display: grid;
  gap: 0.125rem;
  grid-template-columns: repeat(7, 1fr);
  margin-top: 0.5rem;
}
.range-cal__dow {
  color: var(--color-fg-faint);
  font-size: 0.625rem;
  font-weight: 700;
  padding-bottom: 0.125rem;
  text-align: center;
}
.range-cal__day {
  color: var(--color-fg);
  font-size: 0.75rem;
  font-variant-numeric: tabular-nums;
  font-weight: 500;
  height: 1.75rem;
  min-height: 1.75rem;
  padding: 0;
  width: 100%;
}
.range-cal__day--outside { color: var(--color-fg-faint); }
.range-cal__day--today { font-weight: 700; }
.range-cal__day--between,
.range-cal__day--between:hover {
  background: var(--color-blue-50);
  color: var(--color-blue-800);
}
.range-cal__day--edge,
.range-cal__day--edge:hover {
  background: var(--color-primary);
  color: var(--color-primary-fg);
  font-weight: 700;
}
.range-cal__day:disabled { color: var(--color-fg-faint); opacity: 0.45; }

.range-cal__foot {
  align-items: center;
  border-top: 1px solid var(--color-border-subtle);
  display: flex;
  gap: 0.625rem;
  justify-content: space-between;
  margin-top: 0.5625rem;
  padding-top: 0.5625rem;
}
.range-cal__hint {
  color: var(--color-fg-faint);
  font-size: 0.6875rem;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.range-cal__clear { flex-shrink: 0; font-size: 0.75rem; }

/* Below the two panes' combined width there is no room to sit them side by
   side, so the rail becomes a header strip above the calendar. */
@media (max-width: 26rem) {
  .range-presets {
    border-bottom: 1px solid var(--color-border-subtle);
    border-right: 0;
    flex-direction: row;
    flex-wrap: wrap;
    width: 100%;
  }
  .range-preset { width: auto; }
  .range-cal { width: 100%; }
}
</style>

<!-- NOT scoped: `.ds-popover` is rendered by Popover.vue, so it carries that
     component's data-v, not this one's. Its text padding would break the
     full-height divider between the two panes, and its 20rem cap is narrower
     than the two panes need — leaving `width` alone would let the calendar
     spill out of the popover box and off the viewport. -->
<style>
.ds-popover:has(.range-panel) {
  max-width: min(24.5rem, calc(100vw - 1.5rem));
  padding: 0;
  width: max-content;
}
/* On a phone the toolbar wraps, pushing the trigger low enough that the panel
   fits neither below nor above — Reka then places it clipped off the top edge.
   Cap it to the space Reka measured and scroll the remainder. */
.ds-popover:has(.range-panel) {
  max-height: var(--reka-popper-available-height, none);
  overflow-y: auto;
}
</style>
