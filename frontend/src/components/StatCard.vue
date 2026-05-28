<script setup>
defineProps({
  href: { type: String, default: null },
})
</script>

<template>
  <component
    :is="href ? 'router-link' : 'div'"
    :to="href"
    :class="['stat-card', href ? 'stat-card-link focus-visible:outline-2 focus-visible:outline-blue-500' : '']"
  >
    <slot />
  </component>
</template>

<style scoped>
.stat-card-link {
    text-decoration: none;
    transition: box-shadow 150ms, border-color 150ms;
    cursor: pointer;
}
.stat-card-link:hover {
    border-color: #BFDBFE;
    box-shadow: 0 4px 12px rgba(30, 64, 175, 0.08);
}
</style>
