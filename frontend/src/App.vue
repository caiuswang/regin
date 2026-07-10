<script setup>
import { useRoute } from 'vue-router'
import AppLayout from './components/AppLayout.vue'

const route = useRoute()
</script>

<template>
  <router-view v-if="route.meta.public" />
  <AppLayout v-else-if="route.matched.length" />
</template>
