<script setup>
// Hover-revealed "Copy" affordance shared by every conversation card. Owns the
// reveal shell (ml-auto + opacity fade on the parent's `group` hover) and the
// clipboard call; callers pass only the copied `text` and a `tint` (the card's
// own fg/hover-bg palette, since cards render on light and dark surfaces).
import { useCopy } from '../../../composables/useCopy.js'
import Button from '../../ui/Button.vue'
import { cn } from '../../../utils/cn.js'

defineProps({
  text: { type: String, default: '' },
  tint: { type: String, default: 'text-slate-400 hover:bg-slate-700/60' },
})
const { copyText } = useCopy()
</script>

<template>
  <Button
    variant="ghost"
    size="sm"
    :class="cn('ml-auto opacity-0 group-hover:opacity-100 transition-opacity text-[10px]', tint)"
    title="Copy"
    @click.stop="copyText(text)"
  >Copy</Button>
</template>
