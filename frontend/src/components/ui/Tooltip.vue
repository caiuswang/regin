<script setup>
// Accessible tooltip (the app had none — only raw title= attributes).
// Default slot is the trigger; `content` is the tip text.
import { TooltipProvider, TooltipRoot, TooltipTrigger, TooltipPortal, TooltipContent, TooltipArrow } from 'reka-ui'

defineProps({
  content: { type: String, default: '' },
  side: { type: String, default: 'top' },
  delay: { type: Number, default: 300 },
})
</script>

<template>
  <TooltipProvider :delay-duration="delay">
    <TooltipRoot>
      <TooltipTrigger as-child>
        <slot />
      </TooltipTrigger>
      <TooltipPortal>
        <TooltipContent :side="side" :side-offset="6" class="ds-tooltip">
          {{ content }}
          <TooltipArrow class="ds-tooltip-arrow" :width="10" :height="5" />
        </TooltipContent>
      </TooltipPortal>
    </TooltipRoot>
  </TooltipProvider>
</template>

<!-- NOT scoped: Reka portals this content to <body>, beyond scoped data-v. -->
<style>
.ds-tooltip {
  z-index: var(--z-tooltip);
  background: var(--color-slate-900);
  color: var(--color-slate-50);
  font-size: 0.75rem;
  padding: 0.3125rem 0.5rem;
  border-radius: var(--radius-md);
  box-shadow: var(--ds-shadow-md);
  max-width: 18rem;
}
.ds-tooltip-arrow { fill: var(--color-slate-900); }
</style>
