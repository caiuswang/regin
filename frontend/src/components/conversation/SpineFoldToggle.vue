<script setup>
// The turn's fold control, in the spine gutter.
//
// Folding: a turn's spine is routinely dozens of rows long, so whatever opened
// it — the prompt card, or a strip — scrolls out of reach, and the only way
// back was to scroll all the way up to it. The fold variant rides the rail
// instead: a zero-height sticky wrapper inside the spine container, so it
// travels the whole turn and parks under the top edge of the scrollport
// wherever the reader is.
//
// Expanding: the same control on a COLLAPSED turn, so the gutter answers both
// halves of the question in one place. It does not ride — a collapsed turn is
// one chips row tall, and there is nothing to scroll past.
//
// Both sit in the GUTTER, on the rail's column, rather than in the content
// column: that is what keeps them clear of every span card, and why folding is
// not a closing strip at the bottom of the run (reachable from one end only)
// or a pill floating over the feed (which covers content).
//
// The offsets are arithmetic, not tuned. Both put the 24px button's centre on
// x = 16, the rail's centre: inside the spine the wrapper's box starts at the
// spine's content edge (15px padding + 2px rule + 15px margin = 32), so it
// needs -28; beside the chips the wrapper starts at the turn block's edge, so
// it needs +4.
//
// 24px rather than the 20px the design drew, and above `.spine-pin`: the
// per-row pin shares this gutter column and carries z-10, so a smaller, lower
// button lost the click to whichever pin the reader's approach had revealed.
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'
import Button from '../ui/Button.vue'

const props = defineProps({
  // Two meanings, and deliberately only two — this control is the TURN's, in
  // every mode and every state. It briefly also meant "open"/"close the strip",
  // and a gutter whose meaning depended on strip state read as a dead button
  // whenever a strip was open. The strip owns its own ▸/▾.
  //   'expand' — open a collapsed turn's spine.
  //   'turn'   — fold the whole turn back to its chips.
  action: { type: String, default: 'turn' },
})
defineEmits(['toggle'])

// Down means "there is more of this turn than you can see"; up means "put it
// away".
const expand = computed(() => props.action === 'expand')
// Where it is rendered, which is NOT the same question as which way the icon
// points: 'expand' is the one that sits beside a collapsed turn's chips, while
// 'turn' rides the spine rail.
const flush = computed(() => props.action === 'expand')
const TITLES = {
  turn: 'Fold this turn back to its chips',
  expand: 'Show this turn\'s spans',
}
</script>

<template>
  <div class="spine-fold" :class="flush ? 'spine-fold-flush' : 'spine-fold-rail'">
    <Button
      variant="ghost"
      size="sm"
      data-testid="spine-fold-toggle"
      :data-action="action"
      class="spine-fold-btn"
      :title="TITLES[action]"
      @click="$emit('toggle')"
    >
      <Icon :name="expand ? 'chevron-down' : 'chevrons-up'" :size="14" />
    </Button>
  </div>
</template>

<style scoped>
/* Zero height so it takes no room and cannot push a row down. Above
   `.spine-pin` (z-10), which shares this gutter column, and below the view's
   sticky chrome (z-20): at 30 the button scrolled THROUGH the pinned compact
   bar instead of under it. */
.spine-fold {
  height: 0;
  z-index: 15;
}
/* Sticky against the feed's scrollport — what makes the fold variant ride.
   The offset clears whatever chrome is pinned above it: 0 on the bounded
   desktop layout (the header sits outside the scroller), the compact bar's
   height on phones, where parking at a bare 14px put the button on top of
   the view-mode pills. */
.spine-fold-rail {
  position: sticky;
  top: calc(var(--regin-trace-header-h, 0px) + 14px);
}
.spine-fold-flush {
  position: relative;
}
.spine-fold-btn {
  position: absolute;
  top: 0;
  width: 24px;
  height: 24px;
  min-height: 0;
  padding: 0;
  border-radius: 9999px;
  border: 1px solid var(--color-border-strong);
  background: var(--color-surface);
  color: var(--color-fg-subtle);
  box-shadow: 0 1px 3px rgb(15 23 42 / 12%);
}
.spine-fold-rail > .spine-fold-btn { left: -28px; }
.spine-fold-flush > .spine-fold-btn { left: 4px; }
.spine-fold-btn:hover {
  background: var(--color-surface-2);
  color: var(--color-fg);
  border-color: var(--color-fg-faint);
}
</style>
