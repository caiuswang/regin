<script setup>
// Generic popover (click-outside + Escape + focus management + positioning
// from Reka). #trigger slot is the anchor; default slot is the panel body.
import { PopoverRoot, PopoverTrigger, PopoverPortal, PopoverContent, PopoverArrow } from 'reka-ui'
import { usePlScope } from '../../composables/usePipelineTheme'

const open = defineModel('open', { type: Boolean, default: false })
defineProps({
  side: { type: String, default: 'bottom' },
  align: { type: String, default: 'center' },
})
const plScope = usePlScope()
</script>

<template>
  <PopoverRoot v-model:open="open">
    <PopoverTrigger as-child>
      <slot name="trigger" />
    </PopoverTrigger>
    <PopoverPortal>
      <PopoverContent
        :side="side"
        :align="align"
        :side-offset="6"
        class="ds-popover"
        :data-pl-part="plScope ? 'popover' : undefined"
      >
        <slot />
        <PopoverArrow
          class="ds-popover-arrow"
          :width="10"
          :height="5"
          :data-pl-part="plScope ? 'popover-arrow' : undefined"
        />
      </PopoverContent>
    </PopoverPortal>
  </PopoverRoot>
</template>

<!-- NOT scoped: Reka portals this content to <body>, beyond scoped data-v. -->
<style>
.ds-popover {
  z-index: var(--z-popover);
  background: var(--color-surface);
  color: var(--color-fg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--ds-shadow-lg);
  padding: 0.75rem;
  width: max-content;
  max-width: 20rem;
}
.ds-popover-arrow { fill: var(--color-surface); }
</style>
