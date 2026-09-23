import { createRouter, createWebHistory } from 'vue-router'

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
const LibraryView = () => import('./views/LibraryView.vue')
const PipelineView = () => import('./views/PipelineView.vue')
const FeaturesView = () => import('./views/FeaturesView.vue')
const FeatureLedgerView = () => import('./views/FeatureLedgerView.vue')
const DefectsView = () => import('./views/DefectsView.vue')
const DefectDetailView = () => import('./views/DefectDetailView.vue')
const SkillDetailView = () => import('./views/SkillDetailView.vue')
const PromptTemplatesView = () => import('./views/PromptTemplatesView.vue')
const PlansView = () => import('./views/PlansView.vue')
const PlanDetailView = () => import('./views/PlanDetailView.vue')
const SkillReadsView = () => import('./views/SkillReadsView.vue')
const MCPCallsView = () => import('./views/MCPCallsView.vue')
const InboxView = () => import('./views/InboxView.vue')
const MemoryView = () => import('./views/MemoryView.vue')
const IngestErrorsView = () => import('./views/IngestErrorsView.vue')
const TraceView = () => import('./views/TraceView.vue')
const SessionsView = () => import('./views/SessionsView.vue')
const SessionTraceView = () => import('./views/SessionTraceView.vue')
const LiveSessionView = () => import('./views/LiveSessionView.vue')
const TerminalPageView = () => import('./views/TerminalPageView.vue')
const SettingsView = () => import('./views/SettingsView.vue')
const LoginView = () => import('./views/LoginView.vue')
const AuditView = () => import('./views/AuditView.vue')
const UsersView = () => import('./views/UsersView.vue')
const TasksView = () => import('./views/TasksView.vue')
const ProjectsView = () => import('./views/ProjectsView.vue')
const ProjectPanelView = () => import('./views/ProjectPanelView.vue')
const TaskDetailView = () => import('./views/TaskDetailView.vue')
const SchemaDriftView = () => import('./views/SchemaDriftView.vue')
const PayloadLogView = () => import('./views/PayloadLogView.vue')
const DesignSystemView = () => import('./views/DesignSystemView.vue')
const NotFoundView = () => import('./views/NotFoundView.vue')
// PROTOTYPE — throwaway (CAI-269). Delete with the branch; never ships.

const routes = [
  { path: '/login', name: 'login', component: LoginView, meta: { public: true } },
  { path: '/', name: 'dashboard', component: DashboardView },
  { path: '/repos', name: 'repos', component: ReposView },
  { path: '/repos/:name', name: 'repo-detail', component: RepoDetailView },
  { path: '/repos/:name/topics', name: 'repo-topics', component: RepoTopicsView },
  { path: '/repos/:name/topics/compare', name: 'repo-topics-compare', component: RevisionCompareView },
  { path: '/rules', name: 'rules', component: RulesView },
  { path: '/rules/triggers', redirect: '/trace/triggers' },
  { path: '/rules/:id', name: 'rule-detail', component: RuleDetailView },
  // A pattern is the source a skill is deployed from, so the two lists are one
  // library behind one shell and one sidebar entry. The children are ABSOLUTE
  // paths: the grouping is new, the URLs are not, so every existing link, test
  // and bookmark still resolves. The detail pages stay SIBLINGS — a document
  // is not a third reading of the list, and it carries its own header.
  {
    path: '/library',
    name: 'library',
    component: LibraryView,
    redirect: '/patterns',
    children: [
      { path: '/patterns', name: 'patterns', component: PatternsView },
      { path: '/skills', name: 'skills', component: SkillsView },
    ],
  },
  { path: '/patterns/:slug(.*)', name: 'pattern-detail', component: PatternDetailView },
  // Tasks, features and defects are three readings of one loop, so they share
  // one shell and one sidebar entry. The children are ABSOLUTE paths: the
  // grouping is new, the URLs are not, and every existing link — in a view, a
  // test, or printed by the CLI — still resolves.
  //
  // A feature's detail is a CHILD, not a sibling: as a sibling it rendered
  // outside the shell, so opening one took the whole tab strip off screen.
  {
    path: '/pipeline',
    name: 'pipeline',
    component: PipelineView,
    redirect: '/tasks',
    children: [
      { path: '/tasks', name: 'tasks', component: TasksView },
      { path: '/features', name: 'features', component: FeaturesView },
      { path: '/features/:id', name: 'feature-ledger', component: FeatureLedgerView },
      { path: '/defects', name: 'defects', component: DefectsView },
      // A defect's permalink is a child of the shell, not a sibling of it:
      // following a row's id must not make the tab strip that framed it
      // disappear.
      { path: '/defects/:id', name: 'defect-detail', component: DefectDetailView },
      { path: '/projects', name: 'projects', component: ProjectsView },
      { path: '/projects/:id', name: 'project-panel', component: ProjectPanelView },
    ],
  },
  { path: '/skills/:id', name: 'skill-detail', component: SkillDetailView },
  { path: '/tags', redirect: '/patterns' },
  { path: '/tags/:name', redirect: (to) => ({ path: '/patterns', query: { tag: to.params.name } }) },
  { path: '/prompt-templates', name: 'prompt-templates', component: PromptTemplatesView },
  { path: '/plans', name: 'plans', component: PlansView },
  { path: '/plans/:filename(.*)', name: 'plan-detail', component: PlanDetailView },
  // fixedViewport: the view owns its own scroll regions, so the app-shell
  // scroller must not also scroll (see AppLayout `.content-scroll-fixed`).
  // `'lg'` applies that only from 1200px up — below it the inbox's two panes
  // collapse to one column and a nested scroller would leave the list a few
  // rows tall under the header, so the page scrolls normally instead.
  { path: '/live/:id?', name: 'live', component: LiveSessionView, meta: { fixedViewport: true } },
  { path: '/inbox', name: 'inbox', component: InboxView, meta: { fixedViewport: 'lg' } },
  { path: '/memory', name: 'memory', component: MemoryView },
  {
    path: '/trace',
    name: 'trace',
    component: TraceView,
    redirect: '/trace/sessions',
    children: [
      { path: 'sessions', component: SessionsView },
      // The transcript, its rails and the follow-up composer are laid out as
      // bounded siblings that scroll inside themselves, so the composer sits
      // BELOW the feed instead of over it. Below the threshold there isn't
      // room to bound them and the shell scrolls as usual.
      { path: 'sessions/:id', component: SessionTraceView, meta: { fixedViewport: 'lg', hideTraceTabs: true } },
      { path: 'triggers', component: TriggersView },
      { path: 'triggers/raw', component: TriggersRawView },
      { path: 'skill-reads', component: SkillReadsView },
      { path: 'mcp-calls', component: MCPCallsView },
      { path: 'ingest-errors', component: IngestErrorsView },
    ],
  },
  { path: '/skill-reads', redirect: '/trace/skill-reads' },
  { path: '/mcp-calls', redirect: '/trace/mcp-calls' },
  // Chromeless (rendered outside AppLayout): a terminal competing with a
  // sidebar for width and keyboard is not a terminal. Still authenticated —
  // `public` is what skips the guard, and this is not that.
  { path: '/terminal/:id', name: 'terminal', component: TerminalPageView,
    meta: { chromeless: true, fixedViewport: true } },
  { path: '/settings', name: 'settings', component: SettingsView },
  { path: '/audit', name: 'audit', component: AuditView },
  { path: '/schema-drift', name: 'schema-drift', component: SchemaDriftView },
  { path: '/payload-log', name: 'payload-log', component: PayloadLogView },
  { path: '/ds', name: 'design-system', component: DesignSystemView },
  { path: '/account', name: 'account', component: UsersView },
  { path: '/tasks/:id', name: 'task-detail', component: TaskDetailView },
  // Minting a machine credential is a settings act, not a page of its own.
  { path: '/api-keys', redirect: { path: '/settings', query: { section: 'api-keys' } } },
  // Catch-all: App.vue only mounts the shell for matched routes, so an
  // unmatched path would otherwise render a blank page with no way out.
  { path: '/:pathMatch(.*)*', name: 'not-found', component: NotFoundView },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// Auth guard: redirect to /login if no token (unless route is public).
router.beforeEach(async (to) => {
  if (to.meta.public) return true
  const token = localStorage.getItem('regin_auth_token')
  if (!token) return { name: 'login' }
  // Trace (session list + transcripts) is admin-only — the backend enforces
  // this via ADMIN_API_ENDPOINTS; redirect non-admins here so a deep link
  // lands on the dashboard instead of a 403 error page.
  if (to.path === '/trace' || to.path.startsWith('/trace/')
      || to.path.startsWith('/terminal/')) {
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
