<script setup>
import { computed } from 'vue'
import NavIcon from './NavIcon.vue'
import Button from './ui/Button.vue'
import { useTheme } from '../composables/useTheme'
import { useSidebarCollapsed } from '../composables/useSidebarCollapsed'

const props = defineProps({
  navGroups: { type: Array, required: true },
  user: { type: Object, default: null },
  mode: { type: String, default: 'standalone' },
  isActiveLink: { type: Function, required: true },
})

const emit = defineEmits(['navigate', 'open-palette', 'logout'])

const { theme, toggleTheme } = useTheme()
const { collapsed, toggleCollapsed } = useSidebarCollapsed()

const themeLabel = computed(() => (theme.value === 'dark' ? 'Light mode' : 'Dark mode'))
const themeAction = computed(() =>
  theme.value === 'dark' ? 'Switch to light theme' : 'Switch to dark theme')

const userInitials = computed(() => {
  if (!props.user) return ''
  const name = props.user.display_name || props.user.username || ''
  return name.split(/\s+/).map(p => p[0]).filter(Boolean).slice(0, 2).join('').toUpperCase()
})

// The rail hides link text when collapsed, so the accessible name has to carry
// both the label and any badge count that is no longer rendered as a number.
function linkName(link) {
  const count = link.badge?.()
  return count ? `${link.label} (${count})` : link.label
}
</script>

<template>
  <aside class="sidebar floating-card" :class="{ 'is-collapsed': collapsed }">
    <!-- Brand -->
    <div class="sb-brand">
      <!-- The mark itself links home: when collapsed it is the only thing left
           of the brand block, and no nav item routes to `/`. -->
      <router-link
        to="/"
        class="sb-brand-mark no-underline focus-visible:outline-2 focus-visible:outline-blue-500"
        aria-label="regin — dashboard"
      >r</router-link>
      <div v-if="!collapsed" class="min-w-0 flex-1">
        <router-link to="/" class="sb-brand-name no-underline focus-visible:outline-2 focus-visible:outline-blue-500">regin</router-link>
        <div class="sb-brand-meta">{{ mode }}</div>
      </div>
      <Button
        v-if="!collapsed"
        variant="ghost"
        size="icon"
        class="sb-rail-toggle sb-focus focus-visible:outline-2 focus-visible:outline-blue-500"
        aria-label="Collapse sidebar"
        title="Collapse sidebar (\)"
        @click="toggleCollapsed"
      >
        <NavIcon name="panel-collapse" :size="15" />
      </Button>
    </div>
    <div v-if="collapsed" class="sb-rail-toggle-row">
      <Button
        variant="ghost"
        size="icon"
        class="sb-rail-toggle sb-rail-toggle-wide sb-focus focus-visible:outline-2 focus-visible:outline-blue-500"
        aria-label="Expand sidebar"
        title="Expand sidebar (\)"
        @click="toggleCollapsed"
      >
        <NavIcon name="panel-expand" :size="15" />
      </Button>
    </div>

    <!-- Search palette trigger -->
    <div class="sb-search-wrap">
      <Button
        variant="ghost"
        size="md"
        class="sb-search sb-focus focus-visible:outline-2 focus-visible:outline-blue-500"
        aria-label="Open quick search"
        title="Quick search (⌘K)"
        @click="emit('open-palette')"
      >
        <NavIcon name="search" :size="17" />
        <template v-if="!collapsed">
          <span class="sb-search-label">Quick search</span>
          <kbd class="sb-search-kbd">⌘K</kbd>
        </template>
      </Button>
    </div>

    <nav class="sb-nav">
      <div
        v-for="group in navGroups"
        :key="group.label"
        class="sb-group"
        role="group"
        :aria-label="group.label"
      >
        <div v-if="!collapsed" class="sb-section-label">{{ group.label }}</div>
        <div v-else class="sb-section-rule" aria-hidden="true" />
        <Button
          v-for="link in group.links"
          :key="link.to"
          variant="ghost"
          size="md"
          class="sb-item sb-focus focus-visible:outline-2 focus-visible:outline-blue-500"
          :class="{ 'is-active': isActiveLink(link) }"
          :aria-label="linkName(link)"
          :title="collapsed ? linkName(link) : undefined"
          :aria-current="isActiveLink(link) ? 'page' : undefined"
          @click="emit('navigate', link)"
        >
          <NavIcon :name="link.icon" />
          <span v-if="!collapsed" class="sb-item-label">{{ link.label }}</span>
          <span
            v-if="!collapsed && link.badge?.()"
            class="sb-badge"
            :class="`sb-badge-${link.tone?.() || 'info'}`"
          >{{ link.badge() }}</span>
          <span
            v-else-if="collapsed && link.badge?.()"
            class="sb-badge-dot"
            :class="`sb-badge-dot-${link.tone?.() || 'info'}`"
            aria-hidden="true"
          />
        </Button>
      </div>
    </nav>

    <!-- Footer: theme + identity -->
    <div class="sb-user-wrap">
      <Button
        variant="ghost"
        size="md"
        class="sb-theme-toggle sb-focus focus-visible:outline-2 focus-visible:outline-blue-500"
        :aria-label="themeAction"
        :title="themeAction"
        @click="toggleTheme"
      >
        <NavIcon :name="theme === 'dark' ? 'sun' : 'moon'" />
        <span v-if="!collapsed">{{ themeLabel }}</span>
      </Button>
      <div v-if="user" class="sb-user">
        <router-link
          v-if="collapsed"
          to="/account"
          class="sb-user-avatar sb-user-avatar-link no-underline focus-visible:outline-2 focus-visible:outline-blue-500"
          :aria-label="`Account — ${user.display_name || user.username}`"
          :title="user.display_name || user.username"
        >{{ userInitials }}</router-link>
        <template v-else>
          <div class="sb-user-avatar" aria-hidden="true">{{ userInitials }}</div>
          <router-link to="/account" class="sb-user-name no-underline focus-visible:outline-2 focus-visible:outline-blue-500">
            {{ user.display_name || user.username }}
          </router-link>
          <Button
            variant="ghost"
            size="icon"
            class="sb-user-logout sb-focus focus-visible:outline-2 focus-visible:outline-blue-500"
            aria-label="Sign out"
            @click="emit('logout')"
          >
            <NavIcon name="logout" :size="16" />
          </Button>
        </template>
      </div>
    </div>
  </aside>
</template>

<style scoped>
/* The rail's controls borrow the Button primitive for its disabled handling and
   keyboard semantics, but the rail's own geometry has to win over the variant's
   height/justify/gap/radius utilities — scoped rules are unlayered, so those are
   pinned explicitly below. The variant's focus RING is the one thing scoped
   geometry can't override by pinning geometry, since it paints via box-shadow:
   `.sb-focus` resets that so the rail shows a single outline, not outline+ring.
   `.sb-item.is-active` already carries its own box-shadow and so never rang. */
.sb-focus:focus-visible {
  outline: 2px solid var(--color-blue-500);
  outline-offset: 2px;
  box-shadow: none;
}

.sidebar {
  width: 15.5rem;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 1rem;
  align-self: flex-start;
  height: calc(100vh - 2rem);
  overflow: hidden;
  transition: width 240ms cubic-bezier(0.4, 0, 0.2, 1);
}

.sidebar.is-collapsed { width: 4.75rem; }

@media (max-width: 767px) {
  .sidebar { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  .sidebar { transition: none; }
}

.sb-brand {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  padding: 1.25rem 0.875rem 0.75rem;
}

.is-collapsed .sb-brand { justify-content: center; }

.sb-brand-mark {
  width: 2.25rem;
  height: 2.25rem;
  flex-shrink: 0;
  border-radius: 0.75rem;
  background: linear-gradient(135deg, var(--color-blue-600), var(--color-blue-900));
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
  color: var(--color-slate-900);
  letter-spacing: -0.01em;
  line-height: 1.1;
  white-space: nowrap;
}

.sb-brand-meta {
  font-size: 0.65rem;
  color: var(--color-slate-500);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-top: 2px;
  white-space: nowrap;
}

.sb-brand-name:focus-visible {
  outline: 2px solid var(--color-blue-500);
  outline-offset: 2px;
  border-radius: 4px;
}

.sb-rail-toggle {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.75rem;
  height: 1.75rem;
  border-radius: 0.5rem;
  border: 1px solid var(--color-slate-200);
  background: var(--color-surface);
  color: var(--color-slate-400);
  cursor: pointer;
  padding: 0;
  transition: background-color 150ms, color 150ms, border-color 150ms;
}

.sb-rail-toggle:hover {
  border-color: var(--color-slate-300);
  color: var(--color-slate-700);
  background: var(--color-slate-50);
}

.sb-rail-toggle-row {
  display: flex;
  justify-content: center;
  padding: 0 0 0.625rem;
}

.sb-rail-toggle-wide { width: 2.25rem; }

.sb-search-wrap { padding: 0 0.75rem 0.5rem; }
.is-collapsed .sb-search-wrap { padding: 0 1rem 0.5rem; }

.sb-search {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 0.5rem;
  width: 100%;
  height: 2.25rem;
  background: var(--color-slate-100);
  border: 1px solid transparent;
  border-radius: 0.75rem;
  padding: 0 0.6875rem;
  transition: background-color 150ms, color 150ms, border-color 150ms;
  color: var(--color-slate-400);
  cursor: pointer;
  font-size: 0.8125rem;
  font-weight: 400;
  text-align: left;
}

.is-collapsed .sb-search { padding: 0; justify-content: center; }

.sb-search:hover {
  background: var(--color-slate-200);
  border-color: var(--color-slate-300);
  color: var(--color-slate-600);
}

.sb-search-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sb-search-kbd {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.625rem;
  color: var(--color-slate-400);
  background: var(--color-surface);
  border: 1px solid var(--color-slate-200);
  padding: 0.0625rem 0.375rem;
  border-radius: 0.25rem;
  flex-shrink: 0;
}

.sb-nav {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 0.25rem 0.75rem 0.5rem;
}

.is-collapsed .sb-nav { padding: 0.25rem 1rem 0.5rem; }

.sb-section-label {
  font-size: 0.625rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--color-slate-400);
  font-weight: 600;
  padding: 0 0.75rem;
  margin: 1rem 0 0.375rem;
  white-space: nowrap;
}

.sb-group:first-child .sb-section-label { margin-top: 0; }

/* The collapsed rail has no room for a group heading; a hairline keeps the
   grouping visible while role="group" + aria-label keeps it announced. */
.sb-section-rule {
  height: 1px;
  background: var(--color-slate-200);
  margin: 0.6875rem 0.5rem 0.5625rem;
}

.sb-group:first-child .sb-section-rule { margin-top: 0.125rem; }

.sb-item {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 0.75rem;
  width: 100%;
  height: auto;
  min-height: 2.375rem;
  padding: 0.5rem 0.625rem;
  margin-bottom: 2px;
  border: 0;
  border-radius: 0.75rem;
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--color-slate-600);
  background: transparent;
  cursor: pointer;
  transition: background-color 150ms, color 150ms, box-shadow 150ms;
  text-align: left;
}

.is-collapsed .sb-item { padding: 0.5rem 0; justify-content: center; }

.sb-item:hover { background: var(--color-slate-100); color: var(--color-slate-900); }

.sb-item.is-active {
  background: linear-gradient(135deg, var(--color-blue-800), var(--color-blue-500));
  color: #fff;
  font-weight: 600;
  box-shadow: 0 4px 12px rgba(30, 64, 175, 0.25);
}

.sb-item.is-active:hover {
  background: linear-gradient(135deg, var(--color-blue-800), var(--color-blue-500));
  color: #fff;
}

.sb-item-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Tone, not decoration: red only while an agent is actually parked, so the
   colour stays worth reacting to. */
.sb-badge {
  flex-shrink: 0;
  margin-left: auto;
  background: var(--color-blue-100);
  color: var(--color-blue-800);
  font-size: 0.625rem;
  font-weight: 600;
  padding: 0.0625rem 0.375rem;
  border-radius: 0.625rem;
  min-width: 1.125rem;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.sb-badge-warning { background: var(--color-amber-100); color: var(--color-amber-800); }
.sb-badge-danger { background: var(--color-red-100); color: var(--color-red-800); }

.sb-item.is-active .sb-badge { background: rgba(255, 255, 255, 0.25); color: #fff; }

.sb-badge-dot {
  position: absolute;
  top: 5px;
  right: 5px;
  width: 8px;
  height: 8px;
  border-radius: 9999px;
  background: var(--color-blue-500);
  border: 2px solid var(--color-surface);
}

.sb-badge-dot-warning { background: var(--color-amber-500); }
.sb-badge-dot-danger { background: var(--color-red-500); }

.sb-user-wrap {
  padding: 0.5rem 0.75rem 1rem;
  border-top: 1px solid var(--color-slate-100);
}

.is-collapsed .sb-user-wrap { padding: 0.5rem 1rem 1rem; }

.sb-theme-toggle {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 0.625rem;
  width: 100%;
  height: auto;
  min-height: 2.25rem;
  margin-bottom: 0.5rem;
  padding: 0.5rem 0.625rem;
  background: transparent;
  border: 0;
  border-radius: 0.625rem;
  color: var(--color-slate-500);
  font-size: 0.8125rem;
  font-weight: 500;
  cursor: pointer;
  transition: background-color 150ms, color 150ms;
}

.is-collapsed .sb-theme-toggle { padding: 0.5rem 0; justify-content: center; }

.sb-theme-toggle:hover { background: var(--color-slate-100); color: var(--color-slate-900); }

.sb-user {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  background: var(--color-slate-50);
  border: 1px solid var(--color-slate-100);
  border-radius: 0.75rem;
  padding: 0.375rem;
}

.is-collapsed .sb-user { justify-content: center; }

.sb-user-avatar {
  width: 1.875rem;
  height: 1.875rem;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--color-pink-600), var(--color-blue-500));
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.7rem;
  font-weight: 600;
  color: #fff;
  flex-shrink: 0;
}

.sb-user-avatar-link:focus-visible {
  outline: 2px solid var(--color-blue-500);
  outline-offset: 2px;
}

.sb-user-name {
  flex: 1;
  min-width: 0;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-slate-900);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sb-user-name:hover { color: var(--color-blue-800); }

.sb-user-name:focus-visible {
  outline: 2px solid var(--color-blue-500);
  outline-offset: 2px;
  border-radius: 4px;
}

.sb-user-logout {
  width: 1.625rem;
  height: 1.625rem;
  background: transparent;
  border: 0;
  padding: 0.25rem;
  color: var(--color-slate-400);
  cursor: pointer;
  border-radius: 0.375rem;
  flex-shrink: 0;
}

.sb-user-logout:hover { color: var(--color-slate-900); background: var(--color-slate-200); }

.sb-nav::-webkit-scrollbar { width: 8px; }
.sb-nav::-webkit-scrollbar-thumb { background: var(--color-slate-300); border-radius: 4px; }
.sb-nav::-webkit-scrollbar-thumb:hover { background: var(--color-slate-400); }
</style>
