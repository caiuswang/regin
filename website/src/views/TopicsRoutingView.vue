<script setup>
import DocSections from '../components/blocks/DocSections.vue'
import { ROUTING } from '../content/topics.js'
</script>

<template>
  <DocSections :sections="ROUTING" />
</template>
