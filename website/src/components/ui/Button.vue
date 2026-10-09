<script setup>
defineProps({
  variant: { type: String, default: 'primary' },
})
</script>

<template>
  <button
    type="button" class="focus-visible:ring"
    :class="variant === 'bare' ? null : variant === 'icon' ? 'icon-btn' : ['btn', `btn-${variant}`]"
  >
    <slot />
  </button>
</template>
