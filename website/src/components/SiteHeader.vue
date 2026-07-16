<script setup>
import { ref } from 'vue'
import SiteIcon from './SiteIcon.vue'
import Button from './ui/Button.vue'

const NAV = [
  { to: '/', label: 'Introduction' },
  { to: '/getting-started', label: 'Getting Started' },
  { to: '/configuration', label: 'Configuration' },
  { to: '/architecture', label: 'Architecture' },
  { to: '/cli', label: 'CLI' },
]

const menuOpen = ref(false)

function toggleTheme() {
  const root = document.documentElement
  const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark'
  root.setAttribute('data-theme', next)
  try { localStorage.setItem('regin-site-theme', next) } catch { /* private mode */ }
}
</script>

<template>
  <header class="site-header">
    <div class="header-inner">
      <RouterLink to="/" class="brand">
        <SiteIcon name="layers" :size="22" />
        <span>regin</span>
        <span class="beta-pill">beta</span>
      </RouterLink>
      <nav id="site-nav" class="main-nav" :class="{ open: menuOpen }" aria-label="Main">
        <RouterLink
          v-for="item in NAV" :key="item.to" :to="item.to"
          @click="menuOpen = false"
        >{{ item.label }}</RouterLink>
      </nav>
      <Button variant="icon" class="theme-toggle" aria-label="Toggle color theme" @click="toggleTheme">
        <SiteIcon name="sun" :size="18" class="icon-sun" />
        <SiteIcon name="moon" :size="18" class="icon-moon" />
      </Button>
      <Button
        variant="icon" class="nav-toggle" aria-label="Toggle navigation menu"
        :aria-expanded="menuOpen" aria-controls="site-nav" @click="menuOpen = !menuOpen"
      >
        <SiteIcon :name="menuOpen ? 'x' : 'menu'" :size="18" />
      </Button>
    </div>
  </header>
</template>
