<script setup>
import DocSections from '../components/blocks/DocSections.vue'
import { CONFIGURATION } from '../content/pages.js'
</script>

<template>
  <DocSections :sections="CONFIGURATION" />
</template>
