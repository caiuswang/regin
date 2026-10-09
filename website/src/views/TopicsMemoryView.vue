<script setup>
import DocSections from '../components/blocks/DocSections.vue'
import { MEMORY } from '../content/topics.js'
</script>

<template>
  <DocSections :sections="MEMORY" />
</template>
