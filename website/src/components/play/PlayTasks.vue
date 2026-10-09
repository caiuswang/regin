<script setup>
import { P_TASKS } from '../../content/play.js'

defineProps({ st: { type: Object, required: true } })
</script>

<template>
  <div class="play-tasks">
    <div
      v-for="([id, name], i) in P_TASKS" :key="id"
      class="play-task" :class="{ 'is-shown': i < st.shown, 'is-ready': i === 0 }"
    >
      <span class="play-task-head"><span class="play-task-id">{{ id }}</span><span class="play-task-st">{{ i === 0 ? 'READY' : 'WAITING' }}</span></span>
      <span class="play-task-name">{{ name }}</span>
    </div>
  </div>
</template>
