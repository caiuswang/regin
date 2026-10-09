<script setup>
defineProps({
  variant: { type: String, default: 'docs', validator: (v) => v === 'home' || v === 'docs' },
})
</script>

<template>
  <header class="site-header">
    <div class="header-inner">
      <RouterLink to="/" class="brand">
        <span class="logo-square" aria-hidden="true" />
        <span class="brand-name">regin</span>
        <span class="brand-badge">{{ variant === 'home' ? 'BETA' : 'DOCS' }}</span>
      </RouterLink>
      <nav class="header-nav" aria-label="Main">
        <RouterLink v-if="variant === 'home'" to="/getting-started" class="header-link">Guide</RouterLink>
        <RouterLink v-else to="/" class="header-link">Home</RouterLink>
        <a href="https://github.com/caiuswang/regin" class="key-outline focus-visible:ring">GITHUB</a>
      </nav>
    </div>
  </header>
</template>
