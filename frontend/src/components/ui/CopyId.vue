<script setup>
// Compact click-to-copy identifier chip. Renders an id in monospace (truncated
// for layout) with a copy glyph; clicking copies the FULL id and acknowledges
// in place — the glyph becomes a check. Used to surface memory ids on the
// memory detail page and the tree detail popover so they can be copied.
import { computed } from 'vue'
import { useCopy } from '../../composables/useCopy.js'
import Button from './Button.vue'
import Icon from './Icon.vue'

const props = defineProps({
  value: { type: String, default: '' },
  label: { type: String, default: 'ID' },
})
const { copyText, isCopied } = useCopy()
const copied = computed(() => isCopied(props.value))
</script>

<template>
  <Button
    v-if="value"
    variant="ghost"
    size="sm"
    class="h-auto max-w-full gap-1 px-1 py-0.5 font-mono text-[11px] text-fg-faint hover:text-fg focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-1"
    :class="copied && 'text-success hover:text-success'"
    :title="copied ? `Copied ${label}` : `Copy ${label}: ${value}`"
    @click.stop="copyText(value)"
  >
    <span class="truncate">{{ value }}</span>
    <Icon :name="copied ? 'check' : 'copy'" :size="11" class="shrink-0" />
  </Button>
</template>
