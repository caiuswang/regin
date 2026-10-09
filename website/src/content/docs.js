// The one list of docs pages (F154 R2). The world map, the page card's HUD,
// PREV/NEXT, the router and document.title all read it; nothing else may
// enumerate docs pages.

export const DOCS_PAGES = [
  {
    path: '/getting-started', view: 'GettingStartedView',
    group: 'START', code: '0-0', hud: 'GETTING STARTED', title: 'Getting Started',
    lead: 'Install, configure, and run a regin instance. regin is in early beta — schemas, settings keys, hook contracts and CLI flags may change without shims; pin a commit if you need stability.',
  },
  {
    path: '/pipeline', view: 'PipelineView',
    group: 'WORLD 1 · THE LOOP', code: '1-1', hud: 'WORKBENCH · SPEC', title: 'Pipeline',
    lead: 'The Workbench layer, start to finish: a feature carries its spec and acceptance tests, is cut into tasks that run in dependency order, is held to a done-claim, and is charged for every defect found against it. This page follows one real feature through it — the Pixel theme the screenshots are wearing.',
  },
  {
    path: '/runs', view: 'RunsView',
    group: 'WORLD 1 · THE LOOP', code: '1-2', hud: 'RUNS · RUN', title: 'Runs',
    lead: 'The Runs layer: a task becomes an agent run you launch from the dashboard, follow live, steer or stop — and the agent reports back to an inbox.',
  },
  {
    path: '/dashboard', view: 'DashboardView',
    group: 'WORLD 1 · THE LOOP', code: '1-3', hud: 'OBSERVABILITY · WATCH', title: 'The dashboard',
    lead: 'regin serve opens a local web dashboard on :8321. Once the hooks are wired in it fills with your real sessions. A tour of the surfaces you will actually use, roughly in sidebar order.',
  },
  {
    path: '/guidance', view: 'GuidanceView',
    group: 'WORLD 1 · THE LOOP', code: '1-4', hud: 'GUIDANCE · COUNT', title: 'Guidance',
    lead: 'The supporting layer under the runs: what the agent is told before it acts, what pushes back when it drifts, and what it remembers from past sessions.',
  },
  {
    path: '/architecture', view: 'ArchitectureView',
    group: 'REFERENCE', code: 'R-1', hud: 'ARCHITECTURE', title: 'Architecture',
    lead: 'A map of what runs where, organised by the same four layers as the rest of this site. The full internals are in ARCHITECTURE.md in the repository.',
  },
  {
    path: '/configuration', view: 'ConfigurationView',
    group: 'REFERENCE', code: 'R-2', hud: 'CONFIGURATION', title: 'Configuration',
    lead: 'Everything regin reads at boot comes from one typed settings model (lib/settings.py, pydantic-settings): the files, the precedence chain, and every block you can tune.',
  },
  {
    path: '/cli', view: 'CliView',
    group: 'REFERENCE', code: 'R-3', hud: 'CLI REFERENCE', title: 'CLI Reference',
    lead: 'Every operation the web dashboard offers — and the maintenance ones it does not — is available from the command line.',
  },
  {
    path: '/topics', view: 'TopicsView',
    group: 'REFERENCE', code: 'R-4', hud: 'TOPIC WIKIS', title: 'Topic Wikis',
    lead: 'Long-lived knowledge about your repo, structured as a reviewable graph of topics — each with the files that matter, a narrative wiki page, and links to related topics — routed to the agent when it needs them.',
  },
  {
    path: '/topics/routing', view: 'TopicsRoutingView', parent: '/topics',
    group: 'REFERENCE', code: 'R-4.1', hud: 'TOPIC ROUTING', title: 'Routing',
    summary: 'How 2–6 keywords resolve to one approved topic — and why a vague query honestly resolves to nothing.',
    lead: 'How a handful of task keywords resolves to one approved topic — deterministically, with no model call — and hands the agent exactly the refs, wiki, and neighbors it needs.',
  },
  {
    path: '/topics/proposals', view: 'TopicsProposalsView', parent: '/topics',
    group: 'REFERENCE', code: 'R-4.2', hud: 'TOPIC PROPOSALS', title: 'Proposals',
    summary: 'An agent drafts topics against your real code; you review, iterate, and apply one topic at a time.',
    lead: 'Topics are drafted by an agent that reads your real code, and applied only after a human review — the pipeline in between is built so nothing self-promotes into the approved graph.',
  },
  {
    path: '/topics/evolution', view: 'TopicsEvolutionView', parent: '/topics',
    group: 'REFERENCE', code: 'R-4.3', hud: 'DRIFT & EVOLUTION', title: 'Drift & Evolution',
    summary: 'Content-drift detection, wiki debt, and the (default-off) machinery that refreshes stale pages.',
    lead: 'Documentation that was true in March and is wrong in July is worse than no documentation — an agent will trust it. regin detects when code moves out from under a wiki, and routes the fix through the same review pipeline that created the page.',
  },
  {
    path: '/topics/memory', view: 'TopicsMemoryView', parent: '/topics',
    group: 'REFERENCE', code: 'R-4.4', hud: 'TOPICS × MEMORY', title: 'Topics × Memory',
    summary: 'The same tree files cross-session lessons, powers coarse-to-fine recall, and learns from bad routes.',
    lead: 'The topic graph and cross-session memory are one system: lessons file themselves under topics, agents recall by walking the tree instead of guessing keywords, and bad routes feed back into better ones.',
  },
]

export function levelLabel(page) {
  return `${page.group === 'REFERENCE' ? 'REF' : 'LEVEL'} ${page.code}`
}

export function pageFor(path) {
  return DOCS_PAGES.find((page) => page.path === path) ?? null
}

export function neighbours(page) {
  const i = DOCS_PAGES.indexOf(page)
  return { prev: DOCS_PAGES[i - 1] ?? null, next: DOCS_PAGES[i + 1] ?? null }
}

// Sub-pages (those with a parent) are listed only while the reader is inside
// that parent's section.
export function worldMap(currentPath) {
  const groups = []
  for (const page of DOCS_PAGES) {
    if (page.parent && currentPath !== page.parent && !currentPath?.startsWith(`${page.parent}/`)) continue
    let group = groups.find((g) => g.label === page.group)
    if (!group) groups.push((group = { label: page.group, pages: [] }))
    group.pages.push(page)
  }
  return groups
}
