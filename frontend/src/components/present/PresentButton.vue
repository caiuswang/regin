<script setup>
// ⤢ — open this span in the presentation reader.
//
// Always in the DOM at the host card's own faint tone, never hover-revealed:
// a control that only exists on :hover is unreachable on touch and invisible
// to a screen reader walking the row.
import Button from '../ui/Button.vue'

defineProps({
  // Colour family of the card this sits on, so the affordance reads as part of
  // the card rather than as floating chrome.
  tint: { type: String, default: 'neutral' },
})
defineEmits(['click'])
</script>

<template>
  <Button
    variant="ghost"
    size="icon"
    class="present-open h-5 w-5 p-0 rounded-md text-xs font-mono"
    :class="`tint-${tint}`"
    aria-label="Open full content"
    title="Open full content (P)"
    @click.stop="$emit('click')"
  >⤢</Button>
</template>

<style scoped>
.present-open { line-height: 1; }
/* The ::after below needs a positioned box to inset against. The spine row
   already gives this button `absolute`, and a scoped `position: relative`
   here would OVERRIDE it — equal specificity, and SFC styles are injected
   after the utility layer — putting the ⤢ back in flow and growing every
   spine row by 20px. So: only when it is not already positioned. */
.present-open:not(.absolute) { position: relative; }

/* 20×20 is a mouse target, and this component's whole argument is that the
   control must survive touch. Growing the box would restripe a 26px spine row
   in every mode, so the HIT AREA grows instead — 36×36 on a coarse pointer,
   with the painted box unchanged and the layout untouched. */
@media (pointer: coarse) {
  .present-open::after {
    content: ''; position: absolute; inset: -8px;
  }
}
.tint-neutral { color: var(--color-slate-300); }
.tint-diff { color: var(--color-orange-300); }
.tint-rule { color: var(--color-emerald-300); }
.tint-subagent { color: var(--color-violet-300); }
.present-open:hover, .present-open:focus-visible { color: var(--color-fg); }
.tint-neutral:hover, .tint-neutral:focus-visible { background: var(--color-slate-100); }
.tint-diff:hover, .tint-diff:focus-visible { background: var(--color-orange-100); }
.tint-rule:hover, .tint-rule:focus-visible { background: var(--color-emerald-100); }
.tint-subagent:hover, .tint-subagent:focus-visible { background: var(--color-violet-100); }
</style>
