<script setup>
import Badge from './Badge.vue'
import Button from './ui/Button.vue'

defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
  description: { type: String, required: true },
  installed: { type: Boolean, default: null },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['toggle'])
</script>

<template>
  <div class="card">
    <div class="flex items-center justify-between">
      <div>
        <div class="font-medium text-gray-900">
          {{ title }}
          <span v-if="subtitle" class="text-gray-400 font-normal">&mdash; {{ subtitle }}</span>
        </div>
        <div class="text-xs text-gray-400 mt-0.5" v-html="description"></div>
        <div class="text-xs mt-1">
          <span v-if="installed === null || loading" class="text-gray-400">Checking...</span>
          <Badge v-else-if="installed" color="green" label="Installed" />
          <Badge v-else color="gray" label="Not installed" />
        </div>
      </div>
      <div v-if="installed !== null && !loading">
        <Button v-if="installed" variant="secondary" size="sm" @click="emit('toggle')">Remove</Button>
        <Button v-else variant="primary" size="sm" @click="emit('toggle')">Install</Button>
      </div>
    </div>
  </div>
</template>
