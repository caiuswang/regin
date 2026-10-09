<script setup>
import Button from '../ui/Button.vue'

defineProps({ track: { type: Array, required: true } })
defineEmits(['go'])
</script>

<template>
  <ol class="play-track" aria-label="Stages">
    <li v-for="(t, i) in track" :key="t.code">
      <Button
        variant="bare" class="play-track-key"
        :class="{ 'is-current': t.current, 'is-ok': t.ok, 'is-locked': t.locked }"
        :disabled="t.locked" :aria-current="t.current ? 'step' : undefined"
        :aria-label="`${t.mark} ${t.label}`"
        @click="$emit('go', i)"
      >
        <span class="play-track-mark">{{ t.mark }}</span>
        <span class="play-track-label">{{ t.label }}</span>
      </Button>
    </li>
  </ol>
</template>
