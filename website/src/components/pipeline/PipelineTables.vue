<script setup>
import { sectionNumber } from '../docs/sections.js'
import { LOOP, SLICES, SURFACES } from '../../content/pipeline.js'

defineProps({
  kind: { type: String, required: true, validator: (v) => ['loop', 'slices', 'surfaces'].includes(v) },
})
</script>

<template>
  <div v-if="kind === 'loop'" class="pl-table">
    <div v-for="([step, where, what], i) in LOOP" :key="step" class="pl-row pl-loop">
      <span class="pl-step">{{ sectionNumber(i) }} {{ step.toUpperCase() }}</span>
      <code class="pl-where">{{ where }}</code>
      <span class="pl-what">{{ what }}</span>
    </div>
  </div>
  <div v-else-if="kind === 'slices'" class="pl-table">
    <div class="pl-row pl-slices pl-head"><span>SLICE</span><span>NAME</span><span>NEEDS</span><span>DELIVERS</span></div>
    <div v-for="[slice, name, deps, delivers] in SLICES" :key="slice" class="pl-row pl-slices">
      <code class="pl-slice">{{ slice }}</code>
      <span class="pl-name">{{ name }}</span>
      <code class="pl-deps">{{ deps }}</code>
      <span>{{ delivers }}</span>
    </div>
  </div>
  <div v-else class="pl-table">
    <div v-for="[where, what] in SURFACES" :key="where" class="pl-row pl-surfaces">
      <code class="pl-where-key">{{ where }}</code>
      <span class="pl-what">{{ what }}</span>
    </div>
  </div>
</template>
