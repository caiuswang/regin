<script setup>
import { SECTION_PAGES } from '../content/topics.js'
</script>

<template>
  <nav class="subnav" aria-label="Topic wiki pages">
    <RouterLink
      v-for="page in SECTION_PAGES" :key="page.to" :to="page.to"
      class="focus-visible:ring"
    >{{ page.label }}</RouterLink>
  </nav>
</template>
