import { createRouter, createWebHistory } from 'vue-router'
import { useFeatures } from './composables/useFeatures'

const DashboardView = () => import('./views/DashboardView.vue')
const ReposView = () => import('./views/ReposView.vue')
const RepoDetailView = () => import('./views/RepoDetailView.vue')
const RepoTopicsView = () => import('./views/RepoTopicsView.vue')
const RevisionCompareView = () => import('./views/RevisionCompareView.vue')
const PatternsView = () => import('./views/PatternsView.vue')
const PatternDetailView = () => import('./views/PatternDetailView.vue')
const RulesView = () => import('./views/RulesView.vue')
const RuleDetailView = () => import('./views/RuleDetailView.vue')
const TriggersView = () => import('./views/TriggersView.vue')
const TriggersRawView = () => import('./views/TriggersRawView.vue')
const SkillsView = () => import('./views/SkillsView.vue')
const SkillDetailView = () => import('./views/SkillDetailView.vue')
const PromptTemplatesView = () => import('./views/PromptTemplatesView.vue')
const ExperimentsView = () => import('./views/ExperimentsView.vue')
const ExperimentDetailView = () => import('./views/ExperimentDetailView.vue')
const PlansView = () => import('./views/PlansView.vue')
const PlanDetailView = () => import('./views/PlanDetailView.vue')
const SkillReadsView = () => import('./views/SkillReadsView.vue')
const MCPCallsView = () => import('./views/MCPCallsView.vue')
const InboxView = () => import('./views/InboxView.vue')
const MemoryView = () => import('./views/MemoryView.vue')
const GradesView = () => import('./views/GradesView.vue')
const IngestErrorsView = () => import('./views/IngestErrorsView.vue')
const TraceView = () => import('./views/TraceView.vue')
const SessionsView = () => import('./views/SessionsView.vue')
const SessionTraceView = () => import('./views/SessionTraceView.vue')
const LiveSessionView = () => import('./views/LiveSessionView.vue')
const SettingsView = () => import('./views/SettingsView.vue')
const LoginView = () => import('./views/LoginView.vue')
const AuditView = () => import('./views/AuditView.vue')
const UsersView = () => import('./views/UsersView.vue')
const SchemaDriftView = () => import('./views/SchemaDriftView.vue')
const PayloadLogView = () => import('./views/PayloadLogView.vue')
const DesignSystemView = () => import('./views/DesignSystemView.vue')
const NotFoundView = () => import('./views/NotFoundView.vue')

const routes = [
  { path: '/login', name: 'login', component: LoginView, meta: { public: true } },
  { path: '/', name: 'dashboard', component: DashboardView },
  { path: '/repos', name: 'repos', component: ReposView },
  { path: '/repos/:name', name: 'repo-detail', component: RepoDetailView },
  { path: '/repos/:name/topics', name: 'repo-topics', component: RepoTopicsView },
  { path: '/repos/:name/topics/compare', name: 'repo-topics-compare', component: RevisionCompareView },
  { path: '/patterns', name: 'patterns', component: PatternsView },
  { path: '/patterns/:slug(.*)', name: 'pattern-detail', component: PatternDetailView },
  { path: '/rules', name: 'rules', component: RulesView },
  { path: '/rules/triggers', redirect: '/trace/triggers' },
  { path: '/rules/:id', name: 'rule-detail', component: RuleDetailView },
  { path: '/skills', name: 'skills', component: SkillsView },
  { path: '/skills/:id', name: 'skill-detail', component: SkillDetailView },
  { path: '/tags', redirect: '/patterns' },
  { path: '/tags/:name', redirect: (to) => ({ path: '/patterns', query: { tag: to.params.name } }) },
  { path: '/prompt-templates', name: 'prompt-templates', component: PromptTemplatesView },
  { path: '/experiments', name: 'experiments', component: ExperimentsView },
  { path: '/experiments/:id', name: 'experiment-detail', component: ExperimentDetailView },
  { path: '/plans', name: 'plans', component: PlansView },
  { path: '/plans/:filename(.*)', name: 'plan-detail', component: PlanDetailView },
  { path: '/live/:id?', name: 'live', component: LiveSessionView },
  { path: '/inbox', name: 'inbox', component: InboxView },
  { path: '/memory', name: 'memory', component: MemoryView },
  { path: '/grades', name: 'grades', component: GradesView },
  {
    path: '/trace',
    name: 'trace',
    component: TraceView,
    redirect: '/trace/sessions',
    children: [
      { path: 'sessions', component: SessionsView },
      { path: 'sessions/:id', component: SessionTraceView },
      { path: 'triggers', component: TriggersView },
      { path: 'triggers/raw', component: TriggersRawView },
      { path: 'skill-reads', component: SkillReadsView },
      { path: 'mcp-calls', component: MCPCallsView },
      { path: 'ingest-errors', component: IngestErrorsView },
    ],
  },
  { path: '/skill-reads', redirect: '/trace/skill-reads' },
  { path: '/mcp-calls', redirect: '/trace/mcp-calls' },
  { path: '/settings', name: 'settings', component: SettingsView },
  { path: '/audit', name: 'audit', component: AuditView },
  { path: '/schema-drift', name: 'schema-drift', component: SchemaDriftView },
  { path: '/payload-log', name: 'payload-log', component: PayloadLogView },
  { path: '/ds', name: 'design-system', component: DesignSystemView },
  { path: '/account', name: 'account', component: UsersView },
  // Catch-all: App.vue only mounts the shell for matched routes, so an
  // unmatched path would otherwise render a blank page with no way out.
  { path: '/:pathMatch(.*)*', name: 'not-found', component: NotFoundView },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// Auth guard: redirect to /login if no token (unless route is public).
// Also gates /experiments/* behind the experimental_conceal feature flag.
router.beforeEach(async (to) => {
  if (to.meta.public) return true
  const token = localStorage.getItem('regin_auth_token')
  if (!token) return { name: 'login' }
  if (to.path === '/experiments' || to.path.startsWith('/experiments/')) {
    const { features, ready } = useFeatures()
    await ready
    if (!features.experimental_conceal) return { name: 'dashboard' }
  }
  // Trace (session list + transcripts) is admin-only — the backend enforces
  // this via ADMIN_API_ENDPOINTS; redirect non-admins here so a deep link
  // lands on the dashboard instead of a 403 error page.
  if (to.path === '/trace' || to.path.startsWith('/trace/')) {
    let role = null
    try { role = JSON.parse(localStorage.getItem('regin_auth_user') || 'null')?.role } catch { /* ignore */ }
    if (role !== 'admin') return { name: 'dashboard' }
  }
  return true
})

// Route chunks are fetched on demand, so a tab left open across a rebuild asks
// for a content hash that no longer exists and the navigation dies silently
// (Vue Router swallows the rejection). Reload once onto the target so the new
// index.html + manifest are picked up; the one-shot flag stops a reload loop
// when the chunk is genuinely missing rather than merely stale.
const CHUNK_RELOAD_KEY = 'regin_chunk_reload'
router.onError((err, to) => {
  const msg = String(err?.message || '')
  const stale = /dynamically imported module|Importing a module script failed|error loading dynamically/i.test(msg)
  if (!stale) return
  if (sessionStorage.getItem(CHUNK_RELOAD_KEY) === to.fullPath) return
  sessionStorage.setItem(CHUNK_RELOAD_KEY, to.fullPath)
  window.location.assign(to.fullPath)
})
router.afterEach(() => sessionStorage.removeItem(CHUNK_RELOAD_KEY))

export default router
