<script setup>
import InlineText from './InlineText.vue'
import { sectionNumber } from '../docs/sections.js'

defineProps({
  items: { type: Array, required: true },
  ordered: { type: Boolean, default: false },
})
</script>

<template>
  <component :is="ordered ? 'ol' : 'ul'" class="block-list">
    <li v-for="(item, i) in items" :key="i">
      <span class="block-list-mark" aria-hidden="true">{{ ordered ? sectionNumber(i) : '■' }}</span>
      <span><InlineText :text="item" /></span>
    </li>
  </component>
</template>
