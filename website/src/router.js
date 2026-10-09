import { createRouter, createWebHistory, START_LOCATION } from 'vue-router'
import { DOCS_PAGES } from './content/docs.js'

const views = import.meta.glob('./views/*View.vue')

const routes = [
  { path: '/', name: 'home', component: () => import('./views/HomeView.vue') },
  ...DOCS_PAGES.map((page) => ({
    path: page.path,
    name: page.path.slice(1).replace('/', '-'),
    component: views[`./views/${page.view}.vue`],
    meta: { page },
  })),
  { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('./views/NotFoundView.vue'), meta: { title: 'Page not found' } },
]

export const HEADER_OFFSET = 76

export function scrollBehaviour() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
}

const router = createRouter({
  history: createWebHistory(),
  routes,
  async scrollBehavior(to, from, savedPosition) {
    if (savedPosition) return savedPosition
    if (!to.hash) return { top: 0 }
    // A deep link measures its target once the web fonts have swapped in;
    // measured earlier, the reflow moves the section off the 76px line. A
    // first load jumps, it does not animate from the top.
    await document.fonts.ready
    const behavior = from === START_LOCATION ? 'instant' : scrollBehaviour()
    return { el: to.hash, top: HEADER_OFFSET, behavior }
  },
})

const BASE_TITLE = 'regin — a workbench for building software with AI coding agents'

router.afterEach((to) => {
  const title = to.meta.page?.title ?? to.meta.title
  document.title = title ? `${title} · regin` : BASE_TITLE
})

export default router
