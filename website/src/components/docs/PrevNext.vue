<script setup>
import { computed } from 'vue'
import { neighbours } from '../../content/docs.js'

const props = defineProps({
  page: { type: Object, required: true },
})

const around = computed(() => neighbours(props.page))
</script>

<template>
  <nav class="prev-next" aria-label="Previous and next">
    <RouterLink v-if="around.prev" :to="around.prev.path" class="prev-next-card is-prev">
      <span class="prev-next-kicker">◀ PREV · {{ around.prev.code }}</span>
      <span class="prev-next-title">{{ around.prev.title }}</span>
    </RouterLink>
    <RouterLink v-if="around.next" :to="around.next.path" class="prev-next-card is-next">
      <span class="prev-next-kicker">NEXT · {{ around.next.code }} ▶</span>
      <span class="prev-next-title">{{ around.next.title }}</span>
    </RouterLink>
  </nav>
</template>
