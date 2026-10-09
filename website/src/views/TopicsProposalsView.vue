<script setup>
import DocSections from '../components/blocks/DocSections.vue'
import { PROPOSALS } from '../content/topics.js'
</script>

<template>
  <DocSections :sections="PROPOSALS" />
</template>
