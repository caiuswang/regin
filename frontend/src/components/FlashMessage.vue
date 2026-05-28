<script setup>
import { useFlash } from '../composables/useFlash'

const { state, clear } = useFlash()
</script>

<template>
  <div
    v-if="state.message"
    :class="['alert', state.type === 'success' ? 'alert-success' : state.type === 'error' ? 'alert-error' : 'alert-info']"
    class="flex items-center justify-between"
  >
    <span>{{ state.message }}</span>
    <button type="button" class="ml-4 text-sm opacity-60 hover:opacity-100 cursor-pointer" @click="clear">&times;</button>
  </div>
</template>
