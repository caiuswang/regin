<script setup>
import Badge from './Badge.vue'
import KebabMenu from './KebabMenu.vue'

defineProps({
  name: { type: String, required: true },
  status: { type: Object, default: null },     // { color, label, scope? } | null
  primary: { type: Object, default: null },    // { label, action, variant?, disabled? } | null
  kebab: { type: Array, default: () => [] },   // [{ label, action, danger? }]
})
</script>

<template>
  <article class="channel-row">
    <header class="channel-row-head">
      <div class="channel-row-info">
        <span v-if="status" class="channel-row-status">
          <Badge :color="status.color" :label="status.label" />
          <Badge v-if="status.scope" color="gray" :label="status.scope" />
        </span>
        <h3 class="channel-row-name">{{ name }}</h3>
      </div>
      <div class="channel-row-actions">
        <button
          v-if="primary"
          type="button"
          :class="[
            'btn',
            primary.variant === 'danger' ? 'btn-danger' :
            primary.variant === 'secondary' ? 'btn-secondary' :
            'btn-primary',
            'focus-visible:outline-2 focus-visible:outline-blue-500',
          ]"
          :disabled="primary.disabled"
          @click="primary.action">
          {{ primary.label }}
        </button>
        <KebabMenu v-if="kebab.length" :items="kebab" :aria-label="`${name} actions`" />
      </div>
    </header>
    <div v-if="$slots.default" class="channel-row-body">
      <slot />
    </div>
  </article>
</template>

<style scoped>
.channel-row {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 0.625rem;
  padding: 0.875rem 1rem;
}
.channel-row + .channel-row { margin-top: 0.625rem; }
.channel-row-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
}
.channel-row-info {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  min-width: 0;
}
.channel-row-name {
  font-size: 0.9375rem;
  font-weight: 600;
  color: #0f172a;
  margin: 0;
  line-height: 1.25;
}
.channel-row-actions {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  flex-shrink: 0;
}
.channel-row-body {
  margin-top: 0.75rem;
  padding-top: 0.75rem;
  border-top: 1px solid #f1f5f9;
}
.channel-row-status {
  display: inline-flex;
  gap: 0.25rem;
  align-items: center;
}
</style>
