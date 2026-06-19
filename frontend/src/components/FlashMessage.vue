<script setup>
import { useFlash } from '../composables/useFlash'
import Button from './ui/Button.vue'

const { state, clear } = useFlash()
</script>

<template>
  <div
    v-if="state.message"
    role="status"
    aria-live="polite"
    :class="['alert', state.type === 'success' ? 'alert-success' : state.type === 'error' ? 'alert-error' : 'alert-info']"
    class="flex items-center justify-between"
  >
    <span>{{ state.message }}</span>
    <Button variant="ghost" size="icon" class="ml-4" aria-label="Dismiss" @click="clear">&times;</Button>
  </div>
</template>
