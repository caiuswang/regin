<script>
import { cva } from 'class-variance-authority'

/**
 * The single source of truth for button styling. Exported so links,
 * router-links, or any element that should *look* like a button can
 * borrow the classes: `:class="buttonVariants({ variant: 'link' })"`.
 *
 * Colors come only from the semantic token layer (style.css @theme),
 * never raw palette steps — so primary/danger/etc. stay consistent and
 * dark mode flips for free.
 */
export const buttonVariants = cva(
  'inline-flex items-center justify-center gap-1.5 font-medium rounded-lg border border-transparent ' +
    'transition-colors cursor-pointer whitespace-nowrap select-none ' +
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ' +
    'focus-visible:ring-offset-background disabled:opacity-60 disabled:cursor-not-allowed disabled:pointer-events-none',
  {
    variants: {
      variant: {
        primary:
          'text-primary-fg bg-[linear-gradient(135deg,var(--color-blue-800),var(--color-blue-500))] ' +
          'shadow-[0_1px_2px_rgba(30,64,175,0.25)] hover:shadow-[0_4px_12px_rgba(30,64,175,0.3)]',
        secondary: 'bg-surface-2 text-fg-muted hover:bg-surface-3 hover:text-fg',
        danger: 'bg-danger-soft text-danger hover:bg-danger-soft hover:brightness-95',
        ghost: 'text-fg-muted hover:bg-surface-2 hover:text-fg',
        link: 'text-primary hover:text-primary-active underline-offset-4 hover:underline',
      },
      size: {
        sm: 'h-7 px-2.5 text-xs',
        md: 'h-9 px-3.5 text-[0.8125rem]',
        lg: 'h-10 px-4 text-sm',
        icon: 'h-9 w-9 p-0 text-base',
      },
    },
    compoundVariants: [
      // Link is text-only: strip the box sizing so it sits inline like an <a>.
      { variant: 'link', size: ['sm', 'md', 'lg'], class: 'h-auto px-0' },
    ],
    defaultVariants: { variant: 'secondary', size: 'md' },
  },
)
</script>

<script setup>
import { computed } from 'vue'
import { Primitive } from 'reka-ui'
import { cn } from '../../utils/cn'

const props = defineProps({
  variant: { type: String, default: 'secondary' },
  size: { type: String, default: 'md' },
  loading: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  /** Render as a different element/component (e.g. 'a', RouterLink). */
  as: { type: [String, Object], default: 'button' },
  /** Forwarded type when rendered as a native <button>. */
  type: { type: String, default: 'button' },
  class: { type: null, default: '' },
})

const isNativeButton = computed(() => props.as === 'button')
const isDisabled = computed(() => props.disabled || props.loading)
</script>

<template>
  <Primitive
    :as="as"
    :type="isNativeButton ? type : undefined"
    :disabled="isNativeButton ? isDisabled : undefined"
    :aria-disabled="!isNativeButton && isDisabled ? 'true' : undefined"
    :aria-busy="loading ? 'true' : undefined"
    :data-loading="loading ? '' : undefined"
    :class="cn(buttonVariants({ variant, size }), props.class)"
  >
    <span
      v-if="loading"
      class="inline-block h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-current border-r-transparent"
      aria-hidden="true"
    />
    <slot />
  </Primitive>
</template>
