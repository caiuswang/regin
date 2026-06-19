<script setup>
import { defineModel } from 'vue'
import Button from './ui/Button.vue'

const items = defineModel({ type: Array, default: () => [] })

function add() {
  items.value = [...items.value, '']
}

function remove(index) {
  items.value = items.value.filter((_, i) => i !== index)
}

function update(index, value) {
  const copy = [...items.value]
  copy[index] = value
  items.value = copy
}
</script>

<template>
  <div>
    <div class="flex flex-col gap-1.5 mb-1.5">
      <div v-for="(item, pos) in items" :key="pos" class="flex items-center gap-1.5">
        <input
          type="text"
          :value="item"
          @input="update(pos, $event.target.value)"
          aria-label="Path entry"
          class="text-sm border border-gray-300 rounded-md px-2.5 py-1.5 flex-1 focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
          placeholder="Enter path..."
        >
        <Button variant="ghost" size="icon" aria-label="Remove path" @click="remove(pos)">&times;</Button>
      </div>
    </div>
    <Button variant="link" size="sm" @click="add">+ Add path</Button>
  </div>
</template>
