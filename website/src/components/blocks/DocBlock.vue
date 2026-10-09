<script setup>
import { computed } from 'vue'
import BlockP from './BlockP.vue'
import BlockH3 from './BlockH3.vue'
import BlockCode from './BlockCode.vue'
import BlockNote from './BlockNote.vue'
import BlockShot from './BlockShot.vue'
import BlockList from './BlockList.vue'
import BlockTable from './BlockTable.vue'

const BY_KIND = { p: BlockP, h3: BlockH3, code: BlockCode, note: BlockNote, shot: BlockShot, list: BlockList, table: BlockTable }

const props = defineProps({
  block: { type: Object, required: true },
})

const fields = computed(() => {
  const { k: _kind, ...rest } = props.block
  return rest
})
</script>

<template>
  <component :is="BY_KIND[block.k]" v-bind="fields" />
</template>
