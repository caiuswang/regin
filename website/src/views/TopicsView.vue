<script setup>
import DocSections from '../components/blocks/DocSections.vue'
import { TOPICS } from '../content/pages.js'
</script>

<template>
  <DocSections :sections="TOPICS" />
</template>
