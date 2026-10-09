<script setup>
import DocSections from '../components/blocks/DocSections.vue'
import { RUNS } from '../content/pages.js'
</script>

<template>
  <DocSections :sections="RUNS" />
</template>
