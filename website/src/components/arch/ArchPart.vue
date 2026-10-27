<script setup>
import Button from '../ui/Button.vue'

defineProps({
  part: { type: Object, required: true },
  badges: { type: Array, default: () => [] },
  current: { type: Boolean, default: false },
  selected: { type: Boolean, default: false },
})
defineEmits(['select'])
</script>

<template>
  <Button
    variant="bare" class="arch-part"
    :class="{ 'is-lit': badges.length, 'is-current': current, 'is-selected': selected }"
    :aria-pressed="selected" @click="$emit('select', part.id)"
  >
    <span class="arch-part-name">{{ part.name }}</span>
    <span class="arch-part-sub">{{ part.sub }}</span>
    <span v-if="badges.length" class="arch-badges" :aria-label="`step ${badges.join(' and ')}`">
      <b v-for="n in badges" :key="n">{{ n }}</b>
    </span>
    <slot />
  </Button>
</template>
