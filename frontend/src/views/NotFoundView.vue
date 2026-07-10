<script setup>
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <div class="space-y-5">
    <div class="page-header">
      <div>
        <div class="page-eyebrow">404</div>
        <h1 class="page-title">Page not found</h1>
        <p class="page-subtitle">
          No view matches <code class="text-[13px]">{{ route.fullPath }}</code>.
        </p>
      </div>
    </div>
    <router-link to="/" class="text-link">← Back to the dashboard</router-link>
  </div>
</template>
