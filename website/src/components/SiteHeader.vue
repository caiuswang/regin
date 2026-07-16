<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
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
const isDark = ref(false)
const headerEl = ref(null)
const navToggleEl = ref(null)

function toggleTheme() {
  isDark.value = !isDark.value
  document.documentElement.setAttribute('data-theme', isDark.value ? 'dark' : 'light')
  try { localStorage.setItem('regin-site-theme', isDark.value ? 'dark' : 'light') } catch { /* private mode */ }
}

function onDocumentKeydown(event) {
  if (event.key !== 'Escape' || !menuOpen.value) return
  menuOpen.value = false
  navToggleEl.value?.$el.focus()
}

function onDocumentClick(event) {
  if (menuOpen.value && !headerEl.value?.contains(event.target)) menuOpen.value = false
}

onMounted(() => {
  isDark.value = document.documentElement.getAttribute('data-theme') === 'dark'
  document.addEventListener('keydown', onDocumentKeydown)
  document.addEventListener('click', onDocumentClick)
})
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onDocumentKeydown)
  document.removeEventListener('click', onDocumentClick)
})
</script>

<template>
  <header ref="headerEl" class="site-header">
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
      <Button variant="icon" class="theme-toggle" aria-label="Dark theme" :aria-pressed="isDark" @click="toggleTheme">
        <SiteIcon name="sun" :size="18" class="icon-sun" />
        <SiteIcon name="moon" :size="18" class="icon-moon" />
      </Button>
      <Button
        ref="navToggleEl"
        variant="icon" class="nav-toggle" aria-label="Toggle navigation menu"
        :aria-expanded="menuOpen" aria-controls="site-nav" @click="menuOpen = !menuOpen"
      >
        <SiteIcon :name="menuOpen ? 'x' : 'menu'" :size="18" />
      </Button>
    </div>
  </header>
</template>
