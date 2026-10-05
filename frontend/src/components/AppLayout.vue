<script setup>
import { ref, watch, onMounted, computed, defineAsyncComponent } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import Drawer from 'primevue/drawer'
import ConfirmDialog from './ConfirmDialog.vue'
import FullTextSearch from './search/FullTextSearch.vue'
import TraceSessionRail from './TraceSessionRail.vue'
import { useSearchShortcuts } from '../composables/useSearchShortcuts.js'
import { useQuickCapture } from '../composables/useQuickCapture.js'
import FlashMessage from './FlashMessage.vue'
import AppSidebar from './AppSidebar.vue'
import NavIcon from './NavIcon.vue'
import BlockerBanner from './notifications/BlockerBanner.vue'
import NotificationHost from './notifications/NotificationHost.vue'
import QuickCaptureBubble from './tasks/QuickCaptureBubble.vue'
import api from '../api.js'
import { useDriftSummary } from '../composables/useDriftSummary'
import { useDiagnosticsState } from '../composables/useDiagnosticsState'
import { useInboxUnread } from '../composables/useInboxUnread'
import { useNotificationCenter } from '../composables/useNotificationCenter'
import { notificationTier } from '../constants/inboxTypes'
import { useTheme } from '../composables/useTheme'
import Button from './ui/Button.vue'
import { useFilePreview } from '../composables/useFilePreview'

// Lazy and gated on `open`: the viewer (and, through it, Monaco) must not ride
// in the entry bundle (I4).
const FilePreviewDrawer = defineAsyncComponent(() => import('./files/FilePreviewDrawer.vue'))
const { open: filePreviewOpen } = useFilePreview()

const { theme, toggleTheme } = useTheme()
const { pending: driftPending } = useDriftSummary()
const { enabled: diagEnabled } = useDiagnosticsState()
const { unread: inboxUnread, severity: inboxSeverity } = useInboxUnread()
const { setSuppressed, refreshBlockers, bannerVisible } = useNotificationCenter()

// The badge colour is the loudest thing still unread — red while an agent is
// parked, amber for something you should look at, otherwise the plain count.
const inboxTone = computed(() => {
  const tier = notificationTier(inboxSeverity.value)
  if (tier === 1) return 'danger'
  return tier === 2 ? 'warning' : 'info'
})

const router = useRouter()
const route = useRoute()
const user = ref(api.getStoredUser())
const isAdmin = computed(() => user.value?.role === 'admin')
const mode = ref('standalone')
const navOpen = ref(false)
const { openSearch, closeSearch } = useSearchShortcuts()
const { open: captureOpen } = useQuickCapture()

watch(() => route.path, () => {
  user.value = api.getStoredUser()
  navOpen.value = false
  closeSearch()
})

// Pages that *are* the notification queue don't pop over themselves — you are
// already looking at it. Counts still rise; nothing is auto-marked read.
const SUPPRESSED_ROUTES = ['/inbox', '/live']
watch(() => route.path, (path) => {
  setSuppressed(SUPPRESSED_ROUTES.some(prefix => path.startsWith(prefix)))
  // Re-read the parked set on arrival. Not a poll — the stream still carries
  // every change; this only covers the window a stream frame cannot: the page
  // that was loaded, or navigated to, after the agent already stopped.
  refreshBlockers()
}, { immediate: true })

// The tab title is the one notification surface that survives a hidden tab.
watch(inboxUnread, (count) => {
  const base = document.title.replace(/^\(\d+\)\s*/, '')
  document.title = count ? `(${count}) ${base}` : base
}, { immediate: true })

onMounted(async () => {
  try {
    const me = await api.get('/auth/me')
    if (me.mode) mode.value = me.mode
  } catch { /* ignore */ }
})

function openPalette() {
  navOpen.value = false
  openSearch()
}

// The drawer's copy of the bubble. A phone has no keyboard, and on /live the
// bubble itself stands down — without this, capture would be reachable there
// only by navigating away, which is the interruption it exists to remove.
function openCapture() {
  navOpen.value = false
  captureOpen.value = true
}

function handleLogout() {
  api.logout()
}

// `match` is for an entry that fronts several routes: the highlight has to
// survive a tab switch inside the shell AND a drill-down to a detail page, so
// it is a prefix test over the whole set rather than over `to` alone.
function isActiveLink(link) {
  const paths = link.match || [link.to]
  if (link.exact) return paths.includes(route.path)
  return paths.some(p => route.path === p || route.path.startsWith(`${p}/`))
}

function goTo(link) {
  navOpen.value = false
  if (isActiveLink(link)) return
  router.push(link.to)
}

// Group and within-group order follows assumed usage frequency, not topic
// alphabetization: the groups you check continuously while an agent runs
// (Observability) sit above the ones you open to configure or reference
// something (Library, System).
const navGroups = computed(() => [
  {
    label: 'Observability',
    links: [
      { to: '/live', label: 'Live', icon: 'live' },
      { to: '/inbox', label: 'Inbox', exact: true, icon: 'inbox',
        badge: () => inboxUnread.value, tone: () => inboxTone.value },
      // Sessions carry no per-user owner, so Trace is admin-only (matches
      // the ADMIN_API_ENDPOINTS gate on /api/sessions*).
      ...(isAdmin.value ? [{ to: '/trace', label: 'Trace', icon: 'trace' }] : []),
      { to: '/memory', label: 'Memory', exact: true, icon: 'memory' },
      { to: '/audit', label: 'Audit', icon: 'audit' },
    ],
  },
  {
    label: 'Engineering',
    links: [
      // Tasks, features and defects are one workbench behind one entry — the
      // switcher is the tab strip in PipelineView. No role conditional: the
      // ledger reads are authenticated-any-role.
      { to: '/overview', label: 'Pipeline', icon: 'pipeline',
        match: ['/overview', '/tasks', '/features', '/defects'] },
      { to: '/rules', label: 'Rules', exact: true, icon: 'rules' },
      { to: '/plans', label: 'Plans', icon: 'plans' },
    ],
  },
  {
    label: 'Library',
    links: [
      // Patterns and skills are one library behind one entry — a skill is
      // deployed FROM a pattern, so two rail items described one thing twice.
      // The switcher is the tab strip in LibraryView, not the rail.
      { to: '/patterns', label: 'Patterns & Skills', icon: 'patterns',
        match: ['/patterns', '/skills'] },
      { to: '/repos', label: 'Repos', icon: 'repos' },
      { to: '/prompt-templates', label: 'Prompts', icon: 'prompts' },
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
      // API keys are a section of Settings, not a rail entry: minting a
      // machine credential is configuration. `match` keeps the entry lit on
      // the legacy /api-keys URL while its redirect resolves.
      { to: '/settings', label: 'Settings', icon: 'settings',
        match: ['/settings', '/api-keys'] },
    ],
  },
])
</script>

<template>
  <div class="app-shell">
    <div class="app-grid">
      <!-- FLOATING SIDEBAR (desktop) -->
      <AppSidebar
        :nav-groups="navGroups"
        :user="user"
        :mode="mode"
        :is-active-link="isActiveLink"
        @navigate="goTo"
        @open-palette="openPalette"
        @logout="handleLogout"
      />

      <!-- FLOATING CONTENT -->
      <main class="content floating-card">
        <!-- Mobile top bar -->
        <div class="mobile-bar">
          <Button
            variant="ghost"
            size="icon"
            class="mobile-menu-btn focus-visible:outline-2 focus-visible:outline-blue-500"
            aria-label="Open navigation"
            @click="navOpen = true"
          >
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
          </Button>
          <router-link to="/" class="mobile-brand no-underline">regin</router-link>
        </div>

        <div
          class="content-scroll"
          :class="{
            'content-scroll-fixed': route.meta.fixedViewport === true,
            'content-scroll-fixed-lg': route.meta.fixedViewport === 'lg',
            'content-scroll-blocker-inset': bannerVisible,
          }"
        >
          <FlashMessage />
          <!-- Tier 1 sits above the page, inside the scroller, so it scrolls
               with the content rather than covering it. -->
          <BlockerBanner />
          <router-view />
        </div>

        <!-- The ambient half of the switcher. A flex sibling of the scroller,
             so it takes its band out of `main.content` rather than covering
             the last rows of the trace. It renders itself only on a trace
             view. -->
        <TraceSessionRail />
      </main>
    </div>

    <!-- Tier 2 floats over everything; tier 1's mobile sheet portals itself. -->
    <NotificationHost />

    <!-- Mobile drawer -->
    <Drawer v-model:visible="navOpen" position="left" class="!w-72" header="regin">
      <Button
        variant="ghost"
        size="md"
        class="drawer-tool focus-visible:outline-2 focus-visible:outline-blue-500"
        @click="openPalette"
      >
        <NavIcon name="search" :size="16" />
        <span class="drawer-tool-label">Quick search</span>
        <kbd class="drawer-kbd">⌘K</kbd>
      </Button>
      <Button
        variant="ghost"
        size="md"
        class="drawer-tool focus-visible:outline-2 focus-visible:outline-blue-500"
        data-testid="drawer-capture-idea"
        @click="openCapture"
      >
        <NavIcon name="idea" :size="16" />
        <span class="drawer-tool-label">New idea</span>
      </Button>
      <nav class="drawer-nav">
        <div v-for="group in navGroups" :key="group.label" role="group" :aria-label="group.label">
          <div class="drawer-section-label">{{ group.label }}</div>
          <router-link
            v-for="link in group.links"
            :key="link.to"
            :to="link.to"
            class="drawer-link no-underline"
            :class="{ 'is-active': isActiveLink(link) }"
            :aria-current="isActiveLink(link) ? 'page' : undefined"
          >
            <NavIcon :name="link.icon" :size="17" />
            <span class="drawer-link-label">{{ link.label }}</span>
            <span v-if="link.badge?.()" class="drawer-badge"
                  :class="`drawer-badge-${link.tone?.() || 'info'}`">{{ link.badge() }}</span>
          </router-link>
        </div>
      </nav>
      <Button
        variant="ghost"
        size="md"
        class="drawer-tool drawer-tool-end focus-visible:outline-2 focus-visible:outline-blue-500"
        :aria-label="theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'"
        @click="toggleTheme"
      >
        <NavIcon :name="theme === 'dark' ? 'sun' : 'moon'" :size="16" />
        <span class="drawer-tool-label">{{ theme === 'dark' ? 'Light mode' : 'Dark mode' }}</span>
      </Button>
    </Drawer>

    <FullTextSearch />

    <QuickCaptureBubble
      :inset="bannerVisible"
      :crowded="route.meta.fixedViewport === true"
    />

    <ConfirmDialog />

    <FilePreviewDrawer v-if="filePreviewOpen" />
  </div>
</template>

<style scoped>
.app-shell {
  background: linear-gradient(180deg, var(--color-slate-50) 0%, var(--color-slate-100) 100%);
  min-height: 100vh;
  color: var(--color-slate-900);
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

/* Content ------------------------------------------------------------ */
.content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  height: calc(100vh - 2rem);
  /* `overflow: hidden` only clips descendants this box is the containing
     block for. Static, it clipped nothing absolutely positioned: a Tailwind
     `.sr-only` input deep in the scroller resolved against the initial
     containing block, escaped the clip, and grew the document past 100vh —
     the window then scrolled to a blank slab below the shell.
     `clip`, not `hidden`: a hidden box is still a scroll container, so any
     `scrollIntoView` whose target `.content-scroll` could not bring far
     enough scrolled this box instead and shoved the shell up off its own
     bottom edge, with no scrollbar to bring it back. */
  position: relative;
  overflow: clip;
}

@media (max-width: 767px) {
  .content { height: calc(100vh - 1rem); }
}

.mobile-bar {
  display: none;
  align-items: center;
  gap: 0.5rem;
  padding: 0.625rem 1rem;
  border-bottom: 1px solid var(--color-slate-100);
}

@media (max-width: 767px) {
  .mobile-bar { display: flex; }
}

.mobile-menu-btn {
  background: transparent;
  border: 0;
  padding: 0.5rem;
  margin-left: -0.5rem;
  color: var(--color-slate-600);
  cursor: pointer;
  border-radius: 0.5rem;
}

.mobile-menu-btn:hover { background: var(--color-slate-100); }
.mobile-menu-btn:focus-visible { outline: 2px solid var(--color-blue-500); outline-offset: 1px; }

.mobile-brand {
  display: inline-flex;
  align-items: center;
  min-height: 2.25rem;
  padding-inline: 0.5rem;
  margin-inline: -0.5rem;
  font-weight: 700;
  color: var(--color-slate-900);
  font-size: 1rem;
}

.mobile-brand:focus-visible {
  outline: 2px solid var(--color-blue-500);
  outline-offset: 2px;
  border-radius: 4px;
}

/* Mobile drawer ------------------------------------------------------- */
.drawer-tool {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 0.5rem;
  width: 100%;
  height: auto;
  min-height: 2.25rem;
  margin-bottom: 0.75rem;
  padding: 0.5rem 0.75rem;
  border: 0;
  border-radius: 0.5rem;
  background: var(--color-slate-100);
  color: var(--color-slate-500);
  font-size: 0.875rem;
  font-weight: 500;
  cursor: pointer;
}

.drawer-tool:hover { background: var(--color-slate-200); color: var(--color-slate-900); }
.drawer-tool-end { margin-top: 0.75rem; margin-bottom: 0; }
.drawer-tool-label { flex: 1; min-width: 0; text-align: left; }

.drawer-kbd {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.625rem;
  color: var(--color-slate-500);
  background: var(--color-surface);
  border: 1px solid var(--color-slate-200);
  padding: 0.0625rem 0.25rem;
  border-radius: 0.25rem;
}

.drawer-nav {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.drawer-section-label {
  font-size: 0.625rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--color-slate-400);
  font-weight: 600;
  padding: 0 0.25rem;
  margin-bottom: 0.25rem;
}

.drawer-link {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  min-height: 2.5rem;
  padding: 0.5rem 0.75rem;
  border-radius: 0.5rem;
  font-size: 0.875rem;
  color: var(--color-slate-700);
}

.drawer-link:hover { background: var(--color-slate-100); }

.drawer-link.is-active {
  background: var(--color-blue-50);
  color: var(--color-blue-800);
  font-weight: 500;
}

.drawer-link-label { flex: 1; min-width: 0; }

.drawer-badge {
  background: var(--color-blue-100);
  color: var(--color-blue-800);
  font-size: 0.625rem;
  font-weight: 600;
  padding: 0.0625rem 0.375rem;
  border-radius: 0.625rem;
  font-variant-numeric: tabular-nums;
}

.drawer-badge-warning { background: var(--color-amber-100); color: var(--color-amber-800); }
.drawer-badge-danger { background: var(--color-red-100); color: var(--color-red-800); }

.drawer-link:focus-visible {
  outline: 2px solid var(--color-blue-500);
  outline-offset: 2px;
}

.content-scroll {
  flex: 1;
  overflow-y: auto;
  /* Reserve the vertical-scrollbar gutter always, so content width is
     identical between a short (non-scrolling) page and a tall one — otherwise
     the centered content shifts by the scrollbar width as you move between
     pages, reading as unstable left/right margins. */
  scrollbar-gutter: stable;
  padding: 1.5rem 2rem;
}

/* Routes flagged `meta.fixedViewport` (/live, /inbox): freeze the app-shell
   scroll and drop the padding so the view fills the viewport exactly and only
   its own inner region scrolls. Such a view must re-add whatever padding it
   wants. Compound selector so it beats the scoped `.content-scroll` above.

   Frozen, the scroller can't give back what anything above the view takes: the
   view root is sized to the whole scroller (`height: 100%`), so an in-flow
   banner offsets it and pushes its last rows — the composer, the follow-up —
   past a clipped edge with no scroll left to reveal them. Column flex hands the
   banner its own band and the view the remainder. */
.content-scroll.content-scroll-fixed {
  overflow: hidden;
  padding: 0;
  overscroll-behavior: contain;
  display: flex;
  flex-direction: column;
}
.content-scroll.content-scroll-fixed > * { flex-shrink: 0; }
.content-scroll.content-scroll-fixed > *:last-child { flex: 1; min-height: 0; }

/* Same freeze, but only where there is room for the view's own panes; below
   that the shell scrolls as usual. The padding is handed to the view at every
   width so it doesn't shift across the breakpoint. */
.content-scroll.content-scroll-fixed-lg { padding: 0; }
@media (min-width: 1200px) {
  .content-scroll.content-scroll-fixed-lg {
    overflow: hidden;
    overscroll-behavior: contain;
    display: flex;
    flex-direction: column;
  }
  .content-scroll.content-scroll-fixed-lg > * { flex-shrink: 0; }
  .content-scroll.content-scroll-fixed-lg > *:last-child { flex: 1; min-height: 0; }
}

@media (max-width: 767px) {
  .content-scroll { padding: 1rem; }
  .content-scroll.content-scroll-fixed { padding: 0; }

  /* The phone blocker bar is `position: fixed` at the bottom, so it is out of
     flow and would sit over the last rows of every page — and on a
     fixed-viewport view, which cannot scroll at all, there would be no way to
     reveal what it covers. Reserve its height for as long as it is up. */
  .content-scroll.content-scroll-blocker-inset,
  .content-scroll.content-scroll-fixed.content-scroll-blocker-inset {
    /* 62px: 2px top border + 2×0.5rem padding + the 44px touch targets. */
    padding-bottom: calc(3.875rem + env(safe-area-inset-bottom));
  }
}

.content-scroll::-webkit-scrollbar { width: 8px; height: 8px; }
.content-scroll::-webkit-scrollbar-thumb { background: var(--color-slate-300); border-radius: 4px; }
.content-scroll::-webkit-scrollbar-thumb:hover { background: var(--color-slate-400); }
</style>
