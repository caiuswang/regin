<script setup>
defineProps({
  color: { type: String, default: 'gray' },
  label: { type: String, default: '' },
})
</script>

<template>
  <span :class="`badge badge-${color}`"><slot>{{ label }}</slot></span>
</template>
