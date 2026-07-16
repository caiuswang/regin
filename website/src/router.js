import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'home', component: () => import('./views/HomeView.vue'), meta: { title: '' } },
  { path: '/getting-started', name: 'getting-started', component: () => import('./views/GettingStartedView.vue'), meta: { title: 'Getting Started' } },
  { path: '/configuration', name: 'configuration', component: () => import('./views/ConfigurationView.vue'), meta: { title: 'Configuration' } },
  { path: '/architecture', name: 'architecture', component: () => import('./views/ArchitectureView.vue'), meta: { title: 'Architecture' } },
  { path: '/topics', name: 'topics', component: () => import('./views/TopicsView.vue'), meta: { title: 'Topic Wikis' } },
  { path: '/topics/routing', name: 'topics-routing', component: () => import('./views/TopicsRoutingView.vue'), meta: { title: 'Topic Routing' } },
  { path: '/topics/proposals', name: 'topics-proposals', component: () => import('./views/TopicsProposalsView.vue'), meta: { title: 'Topic Proposals' } },
  { path: '/topics/evolution', name: 'topics-evolution', component: () => import('./views/TopicsEvolutionView.vue'), meta: { title: 'Drift & Evolution' } },
  { path: '/topics/memory', name: 'topics-memory', component: () => import('./views/TopicsMemoryView.vue'), meta: { title: 'Topics × Memory' } },
  { path: '/cli', name: 'cli', component: () => import('./views/CliView.vue'), meta: { title: 'CLI Reference' } },
  { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('./views/NotFoundView.vue'), meta: { title: 'Page not found' } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior(to, _from, savedPosition) {
    if (savedPosition) return savedPosition
    if (to.hash) return { el: to.hash, top: 88 }
    return { top: 0 }
  },
})

const BASE_TITLE = 'regin — harness infrastructure for AI coding agents'

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · regin` : BASE_TITLE
})

export default router
