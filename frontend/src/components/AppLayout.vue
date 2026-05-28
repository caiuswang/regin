<script setup>
import { ref, watch, onMounted, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import Drawer from 'primevue/drawer'
import ConfirmDialog from './ConfirmDialog.vue'
import CommandPalette from './CommandPalette.vue'
import FlashMessage from './FlashMessage.vue'
import api from '../api.js'
import { useFeatures } from '../composables/useFeatures'
import { useDriftSummary } from '../composables/useDriftSummary'
import { useDiagnosticsState } from '../composables/useDiagnosticsState'

const { features } = useFeatures()
const { pending: driftPending } = useDriftSummary()
const { enabled: diagEnabled } = useDiagnosticsState()

const router = useRouter()
const route = useRoute()
const user = ref(api.getStoredUser())
const mode = ref('standalone')
const navOpen = ref(false)
const paletteOpen = ref(false)

watch(() => route.path, () => {
  user.value = api.getStoredUser()
  navOpen.value = false
  paletteOpen.value = false
})

onMounted(async () => {
  try {
    const me = await api.get('/auth/me')
    if (me.mode) mode.value = me.mode
  } catch { /* ignore */ }
})

function openPalette() {
  paletteOpen.value = true
  navOpen.value = false
}

function handleLogout() {
  api.logout()
}

function isActiveLink(link) {
  return link.exact ? route.path === link.to : route.path.startsWith(link.to)
}

function goTo(link) {
  navOpen.value = false
  if (isActiveLink(link)) return
  router.push(link.to)
}

const navGroups = computed(() => [
  {
    label: 'Library',
    links: [
      { to: '/repos', label: 'Repos', icon: 'repos' },
      { to: '/patterns', label: 'Patterns', icon: 'patterns' },
      { to: '/skills', label: 'Skills', icon: 'skills' },
      { to: '/prompt-templates', label: 'Prompts', icon: 'prompts' },
    ],
  },
  {
    label: 'Observability',
    links: [
      { to: '/trace', label: 'Trace', icon: 'trace' },
      { to: '/audit', label: 'Audit', icon: 'audit' },
    ],
  },
  {
    label: 'Engineering',
    links: [
      { to: '/rules', label: 'Rules', exact: true, icon: 'rules' },
      ...(features.experimental_conceal
        ? [{ to: '/experiments', label: 'Experiments', icon: 'experiments' }]
        : []),
      { to: '/plans', label: 'Plans', icon: 'plans' },
    ],
  },
  ...(diagEnabled.value ? [{
    label: 'Diagnostics',
    links: [
      { to: '/schema-drift', label: 'Schema drift', icon: 'agents', badge: () => driftPending.value },
      { to: '/payload-log', label: 'Payload log', icon: 'audit' },
    ],
  }] : []),
  {
    label: 'System',
    links: [
      { to: '/settings', label: 'Settings', icon: 'settings' },
    ],
  },
])

const userInitials = computed(() => {
  if (!user.value) return ''
  const name = user.value.display_name || user.value.username || ''
  return name.split(/\s+/).map(p => p[0]).filter(Boolean).slice(0, 2).join('').toUpperCase()
})
</script>

<template>
  <div class="app-shell">
    <div class="app-grid">
      <!-- FLOATING SIDEBAR (desktop) -->
      <aside class="sidebar floating-card">
        <!-- Brand -->
        <div class="sb-brand">
          <div class="sb-brand-mark">r</div>
          <div class="min-w-0">
            <router-link to="/" class="sb-brand-name no-underline focus-visible:outline-2 focus-visible:outline-blue-500">regin</router-link>
            <div class="sb-brand-meta">{{ mode }}</div>
          </div>
        </div>

        <!-- Search palette trigger -->
        <div class="sb-search-wrap">
          <button
            type="button"
            class="sb-search focus-visible:outline-2 focus-visible:outline-blue-500"
            aria-label="Open quick search"
            @click="openPalette"
          >
            <svg class="sb-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
            <span class="sb-search-label">Quick search</span>
            <kbd class="sb-search-kbd">⌘K</kbd>
          </button>
        </div>

        <nav class="sb-nav">
          <template v-for="group in navGroups" :key="group.label">
            <div class="sb-section-label">{{ group.label }}</div>
            <button
              v-for="link in group.links"
              :key="link.to"
              type="button"
              class="sb-item focus-visible:outline-2 focus-visible:outline-blue-500"
              :class="{ 'is-active': isActiveLink(link) }"
              :aria-current="isActiveLink(link) ? 'page' : undefined"
              @click="goTo(link)"
            >
              <svg class="sb-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                <template v-if="link.icon === 'repos'"><path d="M3 7h18M3 12h18M3 17h18"/></template>
                <template v-else-if="link.icon === 'patterns'"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/></template>
                <template v-else-if="link.icon === 'skills'"><path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3Z"/></template>
                <template v-else-if="link.icon === 'prompts'"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></template>
                <template v-else-if="link.icon === 'trace'"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></template>
                <template v-else-if="link.icon === 'audit'"><path d="M9 12l2 2 4-4"/><path d="M21 12c0 4.97-4.03 9-9 9s-9-4.03-9-9 4.03-9 9-9 9 4.03 9 9z"/></template>
                <template v-else-if="link.icon === 'rules'"><path d="M4 7h16M4 12h10M4 17h7"/></template>
                <template v-else-if="link.icon === 'experiments'"><path d="M10 2v6.2L4 14v8h16v-8l-6-5.8V2"/></template>
                <template v-else-if="link.icon === 'plans'"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></template>
                <template v-else-if="link.icon === 'agents'"><circle cx="12" cy="8" r="4"/><path d="M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1"/></template>
                <template v-else-if="link.icon === 'settings'"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.05a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.05a1.65 1.65 0 0 0 1.82-.33l.06.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.05a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/></template>
              </svg>
              <span>{{ link.label }}</span>
              <span v-if="link.badge && link.badge()" class="sb-badge">{{ link.badge() }}</span>
            </button>
          </template>
        </nav>

        <!-- User pill -->
        <div class="sb-user-wrap">
          <div v-if="user" class="sb-user">
            <div class="sb-user-avatar">{{ userInitials }}</div>
            <router-link to="/account" class="sb-user-name no-underline focus-visible:outline-2 focus-visible:outline-blue-500">
              {{ user.display_name || user.username }}
            </router-link>
            <button
              type="button"
              class="sb-user-logout focus-visible:outline-2 focus-visible:outline-blue-500"
              aria-label="Sign out"
              @click="handleLogout"
            >
              <svg class="sb-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M16 17l5-5-5-5M21 12H9M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/></svg>
            </button>
          </div>
        </div>
      </aside>

      <!-- FLOATING CONTENT -->
      <main class="content floating-card">
        <!-- Mobile top bar -->
        <div class="mobile-bar">
          <button
            type="button"
            class="mobile-menu-btn focus-visible:outline-2 focus-visible:outline-blue-500"
            aria-label="Open navigation"
            @click="navOpen = true"
          >
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
          </button>
          <router-link to="/" class="mobile-brand no-underline">regin</router-link>
        </div>

        <div class="content-scroll">
          <FlashMessage />
          <router-view />
        </div>
      </main>
    </div>

    <!-- Mobile drawer -->
    <Drawer v-model:visible="navOpen" position="left" class="!w-72" header="regin">
      <button
        type="button"
        class="mb-3 w-full flex items-center gap-2 px-3 py-2 rounded-md bg-slate-100 hover:bg-slate-200 text-sm text-slate-500 focus-visible:outline-2 focus-visible:outline-blue-500"
        @click="openPalette"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
        <span>Quick search</span>
        <kbd class="ml-auto text-[10px] font-mono bg-white border border-slate-200 text-slate-500 px-1 rounded">⌘K</kbd>
      </button>
      <nav class="flex flex-col gap-3">
        <div v-for="group in navGroups" :key="group.label">
          <div class="text-[10px] uppercase tracking-wider text-slate-400 font-semibold mb-1 px-1">{{ group.label }}</div>
          <router-link
            v-for="link in group.links"
            :key="link.to"
            :to="link.to"
            class="block px-3 py-2 rounded-md text-sm text-slate-700 hover:bg-slate-100 no-underline"
            :class="{ 'bg-blue-50 text-blue-700 font-medium': isActiveLink(link) }"
          >
            {{ link.label }}
          </router-link>
        </div>
      </nav>
    </Drawer>

    <CommandPalette v-model:open="paletteOpen" />

    <ConfirmDialog />
  </div>
</template>

<style scoped>
.app-shell {
  background: linear-gradient(180deg, #F5F7FB 0%, #EEF2F8 100%);
  min-height: 100vh;
  color: #0F172A;
}

.app-grid {
  display: flex;
  gap: 1rem;
  padding: 1rem;
  min-height: 100vh;
}

@media (max-width: 767px) {
  .app-grid { padding: 0.5rem; gap: 0.5rem; }
}

.floating-card {
  background: #fff;
  border-radius: 18px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04), 0 8px 24px rgba(15, 23, 42, 0.06);
}

/* Sidebar ------------------------------------------------------------ */
.sidebar {
  width: 15rem;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 1rem;
  align-self: flex-start;
  height: calc(100vh - 2rem);
}

@media (max-width: 767px) {
  .sidebar { display: none; }
}

.sb-brand {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  padding: 1.25rem 1.25rem 0.75rem;
}

.sb-brand-mark {
  width: 2.25rem;
  height: 2.25rem;
  border-radius: 0.75rem;
  background: linear-gradient(135deg, #2563EB, #1E3A8A);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.95rem;
  font-weight: 700;
  color: #fff;
  box-shadow: 0 4px 12px rgba(37, 99, 235, 0.25);
}

.sb-brand-name {
  display: block;
  font-size: 0.95rem;
  font-weight: 600;
  color: #0F172A;
  letter-spacing: -0.01em;
  line-height: 1.1;
}

.sb-brand-meta {
  font-size: 0.65rem;
  color: #64748B;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-top: 2px;
}

.sb-brand-name:focus-visible {
  outline: 2px solid #3B82F6;
  outline-offset: 2px;
  border-radius: 4px;
}

.sb-search-wrap { padding: 0 0.75rem 0.5rem; }

.sb-search {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  width: 100%;
  background: #F1F5F9;
  border: 0;
  border-radius: 0.75rem;
  padding: 0.5rem 0.75rem;
  transition: background-color 150ms;
  color: #94A3B8;
  cursor: pointer;
  font: inherit;
  text-align: left;
}

.sb-search:hover { background: #E2E8F0; color: #475569; }

.sb-search-label {
  flex: 1;
  font-size: 0.8125rem;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sb-search-kbd {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.625rem;
  color: #94A3B8;
  background: #fff;
  border: 1px solid #E2E8F0;
  padding: 0.0625rem 0.375rem;
  border-radius: 0.25rem;
  flex-shrink: 0;
}

.sb-nav {
  flex: 1;
  overflow-y: auto;
  padding: 0.25rem 0.75rem 0.5rem;
}

.sb-section-label {
  font-size: 0.625rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #94A3B8;
  font-weight: 600;
  padding: 0 0.75rem;
  margin: 1rem 0 0.375rem;
}

.sb-section-label:first-child { margin-top: 0; }

.sb-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  width: 100%;
  padding: 0.5rem 0.75rem;
  border-radius: 0.75rem;
  font-size: 0.8125rem;
  color: #475569;
  background: transparent;
  border: 0;
  cursor: pointer;
  transition: background-color 150ms, color 150ms;
  text-align: left;
}

.sb-item:hover { background: #F1F5F9; color: #0F172A; }

.sb-item:focus-visible {
  outline: 2px solid #3B82F6;
  outline-offset: 2px;
}

.sb-item.is-active {
  background: linear-gradient(135deg, #1E40AF, #3B82F6);
  color: #fff;
  box-shadow: 0 4px 12px rgba(30, 64, 175, 0.25);
}

.sb-item.is-active .sb-icon { color: #fff; }

.sb-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  color: #64748B;
}

.sb-item:hover .sb-icon { color: #0F172A; }

.sb-badge {
  margin-left: auto;
  background: #FEE2E2;
  color: #991B1B;
  font-size: 0.625rem;
  font-weight: 600;
  padding: 0.0625rem 0.375rem;
  border-radius: 0.625rem;
  min-width: 1.125rem;
  text-align: center;
}

.sb-item.is-active .sb-badge { background: rgba(255, 255, 255, 0.25); color: #fff; }

.sb-user-wrap { padding: 0.5rem 0.75rem 1rem; }

.sb-user {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  background: #F8FAFC;
  border-radius: 0.75rem;
  padding: 0.5rem 0.625rem;
}

.sb-user-avatar {
  width: 2rem;
  height: 2rem;
  border-radius: 50%;
  background: linear-gradient(135deg, #F472B6, #3B82F6);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.7rem;
  font-weight: 600;
  color: #fff;
  flex-shrink: 0;
}

.sb-user-name {
  flex: 1;
  min-width: 0;
  font-size: 0.75rem;
  font-weight: 600;
  color: #0F172A;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sb-user-name:hover { color: #1E40AF; }

.sb-user-name:focus-visible {
  outline: 2px solid #3B82F6;
  outline-offset: 2px;
  border-radius: 4px;
}

.sb-user-logout {
  background: transparent;
  border: 0;
  padding: 0.25rem;
  color: #94A3B8;
  cursor: pointer;
  border-radius: 0.375rem;
}

.sb-user-logout:hover { color: #0F172A; background: #E2E8F0; }

.sb-user-logout:focus-visible {
  outline: 2px solid #3B82F6;
  outline-offset: 1px;
}

/* Content ------------------------------------------------------------ */
.content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  height: calc(100vh - 2rem);
  overflow: hidden;
}

@media (max-width: 767px) {
  .content { height: calc(100vh - 1rem); }
}

.mobile-bar {
  display: none;
  align-items: center;
  gap: 0.5rem;
  padding: 0.625rem 1rem;
  border-bottom: 1px solid #F1F5F9;
}

@media (max-width: 767px) {
  .mobile-bar { display: flex; }
}

.mobile-menu-btn {
  background: transparent;
  border: 0;
  padding: 0.5rem;
  margin-left: -0.5rem;
  color: #475569;
  cursor: pointer;
  border-radius: 0.5rem;
}

.mobile-menu-btn:hover { background: #F1F5F9; }
.mobile-menu-btn:focus-visible { outline: 2px solid #3B82F6; outline-offset: 1px; }

.mobile-brand {
  font-weight: 700;
  color: #0F172A;
  font-size: 1rem;
}

.mobile-brand:focus-visible {
  outline: 2px solid #3B82F6;
  outline-offset: 2px;
  border-radius: 4px;
}

:deep(.drawer-link):focus-visible {
  outline: 2px solid #3B82F6;
  outline-offset: 2px;
}

.content-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 1.5rem 2rem;
}

@media (max-width: 767px) {
  .content-scroll { padding: 1rem; }
}

/* Custom scrollbars within sidebar/content */
.sb-nav::-webkit-scrollbar,
.content-scroll::-webkit-scrollbar { width: 8px; height: 8px; }
.sb-nav::-webkit-scrollbar-thumb,
.content-scroll::-webkit-scrollbar-thumb { background: #CBD5E1; border-radius: 4px; }
.sb-nav::-webkit-scrollbar-thumb:hover,
.content-scroll::-webkit-scrollbar-thumb:hover { background: #94A3B8; }
</style>
