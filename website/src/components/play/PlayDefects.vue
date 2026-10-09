<script setup>
import PixelSprite from './PixelSprite.vue'
import Button from '../ui/Button.vue'
import { BUG_PALETTE, BUG_ROWS, P_DEFECTS } from '../../content/play.js'

defineProps({ st: { type: Object, required: true } })
defineEmits(['fix'])
</script>

<template>
  <p class="play-micro-label play-defects-head">12 DECLARED DURING THE BUILD · 3 OF THEM:</p>
  <div v-for="(text, i) in P_DEFECTS" :key="text" class="play-defect" :class="{ 'is-fixed': st.fixed[i] }">
    <Button variant="bare" class="play-bug" :disabled="st.fixed[i]" :aria-label="`Fix defect: ${text}`" @click="$emit('fix', i)">
      <span class="play-bug-sprite"><PixelSprite :rows="BUG_ROWS" :palette="BUG_PALETTE" /></span>
    </Button>
    <span class="play-defect-text">
      <span class="play-defect-name">{{ text }}</span>
      <span class="play-defect-st">{{ st.fixed[i] ? 'CLOSED · CHECK NAMED' : 'OPEN · CLICK THE BUG' }}</span>
    </span>
  </div>
</template>
