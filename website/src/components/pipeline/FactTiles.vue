<script setup>
import { FACTS } from '../../content/pipeline.js'
</script>

<template>
  <div class="pl-facts">
    <div v-for="[fact, value] in FACTS" :key="fact" class="pl-fact">
      <span class="pl-fact-key">{{ fact }}</span>
      <span class="pl-fact-value">{{ value }}</span>
    </div>
  </div>
</template>
