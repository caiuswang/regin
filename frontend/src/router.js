import { createRouter, createWebHistory } from 'vue-router'
import { useFeatures } from './composables/useFeatures'

import DashboardView from './views/DashboardView.vue'
import ReposView from './views/ReposView.vue'
import RepoDetailView from './views/RepoDetailView.vue'
import RepoTopicsView from './views/RepoTopicsView.vue'
import RevisionCompareView from './views/RevisionCompareView.vue'
import PatternsView from './views/PatternsView.vue'
import PatternDetailView from './views/PatternDetailView.vue'
import RulesView from './views/RulesView.vue'
import RuleDetailView from './views/RuleDetailView.vue'
import TriggersView from './views/TriggersView.vue'
import TriggersRawView from './views/TriggersRawView.vue'
import SkillsView from './views/SkillsView.vue'
import SkillDetailView from './views/SkillDetailView.vue'
import PromptTemplatesView from './views/PromptTemplatesView.vue'
import ExperimentsView from './views/ExperimentsView.vue'
import ExperimentDetailView from './views/ExperimentDetailView.vue'
import PlansView from './views/PlansView.vue'
import PlanDetailView from './views/PlanDetailView.vue'
import SkillReadsView from './views/SkillReadsView.vue'
import MCPCallsView from './views/MCPCallsView.vue'
import InboxView from './views/InboxView.vue'
import MemoryView from './views/MemoryView.vue'
import GradesView from './views/GradesView.vue'
import IngestErrorsView from './views/IngestErrorsView.vue'
import TraceView from './views/TraceView.vue'
import SessionsView from './views/SessionsView.vue'
import SessionTraceView from './views/SessionTraceView.vue'
import LiveSessionView from './views/LiveSessionView.vue'
import SettingsView from './views/SettingsView.vue'
import LoginView from './views/LoginView.vue'
import AuditView from './views/AuditView.vue'
import UsersView from './views/UsersView.vue'
import SchemaDriftView from './views/SchemaDriftView.vue'
import PayloadLogView from './views/PayloadLogView.vue'
import DesignSystemView from './views/DesignSystemView.vue'
import NotFoundView from './views/NotFoundView.vue'

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

export default router
