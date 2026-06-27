<script setup>
// Compact click-to-copy identifier chip. Renders an id in monospace (truncated
// for layout) with a copy glyph; clicking copies the FULL id and flashes
// "Copied!" via the shared useCopy composable. Used to surface memory ids on
// the memory detail page and the tree detail popover so they can be copied.
import { useCopy } from '../../composables/useCopy.js'
import Button from './Button.vue'
import Icon from './Icon.vue'

const props = defineProps({
  value: { type: String, default: '' },
  label: { type: String, default: 'ID' },
})
const { copyText } = useCopy()
</script>

<template>
  <Button
    v-if="value"
    variant="ghost"
    size="sm"
    class="h-auto max-w-full gap-1 px-1 py-0.5 font-mono text-[11px] text-fg-faint hover:text-fg focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-1"
    :title="`Copy ${label}: ${value}`"
    @click.stop="copyText(value)"
  >
    <span class="truncate">{{ value }}</span>
    <Icon name="copy" :size="11" class="shrink-0" />
  </Button>
</template>
