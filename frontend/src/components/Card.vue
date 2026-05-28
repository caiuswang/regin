<script setup>
defineProps({
  noPadding: { type: Boolean, default: false },
})
</script>

<template>
  <div :class="['card', noPadding ? '!p-0 overflow-x-auto' : '']">
    <slot />
  </div>
</template>
