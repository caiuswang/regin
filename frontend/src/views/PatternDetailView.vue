<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'
import Breadcrumb from '../components/Breadcrumb.vue'
import ChannelRow from '../components/ChannelRow.vue'
import PatternTagsEditor from '../components/PatternTagsEditor.vue'
import PatternExperimentCreator from '../components/PatternExperimentCreator.vue'
import PatternContentEditor from '../components/PatternContentEditor.vue'
import PatternDescriptionEditor from '../components/PatternDescriptionEditor.vue'
import PatternProjectsPicker from '../components/PatternProjectsPicker.vue'
import PatternRulesPanel from '../components/PatternRulesPanel.vue'
import { useFlash } from '../composables/useFlash'
import { useConfirm } from '../composables/useConfirm'
import { useFeatures } from '../composables/useFeatures'

const { features } = useFeatures()

const route = useRoute()
const router = useRouter()
const { flash } = useFlash()
const { confirm } = useConfirm()
const data = ref(null)
const loading = ref(true)

const activeTab = ref('content')

// Project deployments
const deployments = ref([])
const repos = ref([])

// Tag editing

// Content editing

// Description editing

// Experiment creation
const expProcessing = ref(null)

const notFound = ref(false)

async function load() {
  // Wiki rows share the pattern_docs table with `source_kind='wiki'`
  // and surface in the patterns dense search, but they have no
  // pattern detail page. Slug shape is `wiki/<repo>/<topic-id>` —
  // route the user to the topics workspace where the wiki preview
  // panel already renders that page. Covers direct URL navigation,
  // stale bookmarks, and any caller that builds /patterns/<slug>
  // links without consulting source_kind.
  const slug = route.params.slug || ''
  if (slug.startsWith('wiki/')) {
    const parts = slug.split('/')
    const repoName = parts[1]
    const topicId = parts.slice(2).join('/')
    if (repoName && topicId) {
      router.replace({
        path: `/repos/${repoName}/topics`,
        query: { tab: 'wiki', topic: topicId },
      })
      return
    }
  }
  try {
    data.value = await api.get(`/patterns/${route.params.slug}`)
    await loadDeployments()
  } catch (err) {
    notFound.value = true
  } finally {
    loading.value = false
  }
}

async function loadRepos() {
  if (repos.value.length) return
  repos.value = (await api.get('/repos')).repos || []
}

async function loadDeployments() {
  if (!data.value?.skill_id) return
  const result = await api.get(`/skills/${data.value.skill_id}/deployments`)
  deployments.value = result?.deployments || []
  await loadRepos()
}

onMounted(load)



async function skillAction(action, opts = {}) {
  const result = await api.post(`/skills/${data.value.skill_id}/${action}`, opts)
  if (!result.ok) {
    if (result.confirm_force) {
      const ok = await confirm('Force push', result.msg, false)
      if (ok) { await skillAction(action, { ...opts, force: true }); return }
    } else {
      flash(result.msg || `Failed to ${action}`, 'error')
    }
    return
  }
  flash(result.msg)
  await load()
}

async function forcePush() {
  const ok = await confirm('Force push', 'Overwrite deployed skill with source version?', false)
  if (!ok) return
  await skillAction('push', { force: true })
}

async function undeploy() {
  const ok = await confirm('Undeploy', 'Remove deployed skill? Source stays.', true)
  if (!ok) return
  await skillAction('undeploy')
}

async function deletePattern() {
  const ok = await confirm('Delete pattern', `Permanently delete "${data.value.doc?.title || route.params.slug}"? This removes the directory, database entry, and unlinks any deployed skill.`, true)
  if (!ok) return
  const result = await api.post(`/patterns/${route.params.slug}/delete`)
  if (!result.ok) { flash(result.msg || 'Failed to delete', 'error'); return }
  flash(result.msg)
  router.push('/patterns')
}

const promoting = ref(false)
const skillhubStatus = ref({ available: false, url: '', reason: 'loading…' })
async function loadSkillhubStatus() {
  try {
    skillhubStatus.value = await api.get('/skillhub-status')
  } catch (_) {
    skillhubStatus.value = { available: false, url: '', reason: 'lookup failed' }
  }
}
onMounted(loadSkillhubStatus)

async function promoteToSkill() {
  const version = window.prompt('Promote to regin-skillhub as version:', '1.0.0')
  if (!version) return
  if (!/^\d+\.\d+\.\d+$/.test(version)) {
    flash('Version must be MAJOR.MINOR.PATCH (e.g. 1.0.0)', 'error')
    return
  }
  promoting.value = true
  try {
    const result = await api.post(`/patterns/${route.params.slug}/promote`, { version })
    if (!result.ok) {
      const reason = result.error || result.msg
      if (reason && (reason.includes('already registered') || reason.includes('already present'))) {
        const overwrite = await confirm('Overwrite version',
          `Version ${version} already registered in regin-skillhub. Overwrite?`, true)
        if (!overwrite) return
        const retry = await api.post(`/patterns/${route.params.slug}/promote`, { version, force: true })
        if (!retry.ok) { flash(retry.error || retry.msg || 'Promote failed', 'error'); return }
        flash(`Promoted ${route.params.slug} → regin-skillhub ${version} (overwritten)`)
        return
      }
      flash(reason || 'Promote failed', 'error')
      return
    }
    flash(`Promoted ${route.params.slug} → regin-skillhub ${version}`)
  } finally {
    promoting.value = false
  }
}


async function activateExp(id) {
  expProcessing.value = id
  for (const e of data.value.experiments) {
    e.active = e.id === id
  }
  updateConcealedTexts()
  const result = await api.post(`/experiments/${id}/activate`)
  expProcessing.value = null
  if (!result.ok) {
    flash(result.msg || 'Failed to activate', 'error')
    await load()
    return
  }
  flash(result.msg)
  await load()
}

async function deactivateExp(id) {
  expProcessing.value = id
  const exp = data.value.experiments.find(e => e.id === id)
  if (exp) exp.active = false
  updateConcealedTexts()
  const result = await api.post(`/experiments/${id}/deactivate`)
  expProcessing.value = null
  if (!result.ok) {
    flash(result.msg || 'Failed to deactivate', 'error')
    await load()
    return
  }
  flash(result.msg)
  await load()
}

function headingToPlain(s) {
  let i = 0
  while (i < s.length && s[i] === '#') i++
  return s.slice(i).trim().split('`').join('')
}

function updateConcealedTexts() {
  const sections = new Set()
  for (const e of data.value.experiments || []) {
    if (e.active) e.sections.forEach(s => sections.add(headingToPlain(s)))
  }
  data.value.concealed_texts = [...sections]
}

const skillBadge = {
  in_sync: { color: 'green', label: 'deployed', scope: 'global' },
  drifted: { color: 'yellow', label: 'out of sync', scope: 'global' },
  source_only: { color: 'purple', label: 'not deployed' },
  project_only: { color: 'green', label: 'deployed', scope: 'project' },
  deployed_only: { color: 'blue', label: 'orphan (source missing)' },
}

// Channel row inputs --------------------------------------------------

const hasRules = computed(() =>
  Boolean(data.value?.enforcing_rules?.length) ||
  Boolean(data.value?.attached_rule_bundles?.length),
)
const hasExperiments = computed(() =>
  features.experimental_conceal
  && Boolean(data.value?.procedure_id)
  && Boolean(data.value?.available_sections?.length),
)
const ruleCount = computed(() => {
  const direct = data.value?.enforcing_rules?.length || 0
  const bundles = (data.value?.attached_rule_bundles || []).reduce((s, b) => s + (b.rules?.length || 0), 0)
  return direct + bundles
})

const globalSkillStatus = computed(() => {
  const b = skillBadge[data.value?.skill_state]
  return b ? { color: b.color, label: b.label, scope: b.scope } : null
})

const globalSkillPrimary = computed(() => {
  if (!data.value?.skill_id) return null
  const state = data.value.skill_state
  if (state === 'drifted') return { label: 'Force push', action: forcePush }
  if (state === 'deployed_only') return { label: 'Pull', action: () => skillAction('pull') }
  return { label: 'Push', action: () => skillAction('push') }
})

const globalSkillKebab = computed(() => {
  const state = data.value?.skill_state
  const items = []
  if (state !== 'deployed_only') items.push({ label: 'Pull from disk', action: () => skillAction('pull') })
  if (state === 'drifted' || state === 'deployed_only') items.push({ label: 'Push', action: () => skillAction('push') })
  if (state === 'in_sync') items.push({ label: 'Force push', action: forcePush })
  if (['in_sync', 'drifted', 'deployed_only'].includes(state)) {
    items.push({ label: 'Undeploy', action: undeploy, danger: true })
  }
  return items
})

const skillhubPrimary = computed(() => ({
  label: promoting.value ? 'Promoting…' : 'Promote',
  action: promoteToSkill,
  disabled: promoting.value,
}))

const enabledRuleCount = computed(() =>
  (data.value?.enforcing_rules || []).filter(r => !r.disabled).length,
)
const disabledRuleCount = computed(() =>
  (data.value?.enforcing_rules || []).filter(r => r.disabled).length,
)

const ruleEnforcementStatus = computed(() => {
  const on = enabledRuleCount.value
  const off = disabledRuleCount.value
  if (!on && !off) return null
  if (!on) return { color: 'gray', label: `${off} disabled` }
  if (off) return { color: 'yellow', label: `${on} on · ${off} off` }
  return { color: 'green', label: `${on} enforced` }
})

// Thin wrappers shared between the rail's "Rule enforcement" ChannelRow
// (here) and PatternRulesPanel (which has its own copies). Kept here
// because the rail's ChannelRow `primary` / `kebab` props take an
// `action` callback that fires from inside ChannelRow itself.
async function disableAllRules() {
  const ok = await confirm(
    'Disable rules',
    `Disable ${enabledRuleCount.value} rule(s)?`,
    true,
  )
  if (!ok) return
  const result = await api.post(`/patterns/${route.params.slug}/rules/disable`)
  if (!result.ok) { flash(result.msg || 'Failed to disable rules', 'error'); return }
  flash(result.msg)
  await load()
}

async function enableAllRules() {
  const result = await api.post(`/patterns/${route.params.slug}/rules/enable`)
  if (!result.ok) { flash(result.msg || 'Failed to enable rules', 'error'); return }
  flash(result.msg)
  await load()
}

const ruleEnforcementPrimary = computed(() => {
  const on = enabledRuleCount.value
  const off = disabledRuleCount.value
  if (on > 0) return { label: `Disable ${on}`, action: disableAllRules, variant: 'danger' }
  if (off > 0) return { label: `Re-enable ${off}`, action: enableAllRules, variant: 'secondary' }
  return null
})

const ruleEnforcementKebab = computed(() => {
  const on = enabledRuleCount.value
  const off = disabledRuleCount.value
  const items = []
  if (on > 0 && off > 0) items.push({ label: `Re-enable ${off} disabled`, action: enableAllRules })
  return items
})

</script>

<template>
  <div v-if="loading" class="empty-state">Loading pattern…</div>
  <div v-else-if="notFound || !data?.doc">
    <Breadcrumb :items="[{ label: 'Patterns', to: '/patterns' }, { label: route.params.slug }]" />
    <Card class="empty-state">
      <h1 class="page-title justify-center">Pattern not found</h1>
      <p class="text-sm text-slate-500 my-3">
        No pattern with slug <code class="cell-code">{{ route.params.slug }}</code> exists.
        It may have been deleted.
      </p>
      <router-link to="/patterns"
        class="btn btn-primary focus-visible:outline-2 focus-visible:outline-blue-500">
        Back to Patterns
      </router-link>
    </Card>
  </div>
  <div v-else>
    <Breadcrumb :items="[
      { label: 'Patterns', to: '/patterns' },
      { label: data.doc.category, to: `/patterns?category=${data.doc.category}` },
      { label: data.doc.title },
    ]" />

    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Pattern · {{ data.doc.category }}</div>
        <h1 class="page-title">
          {{ data.doc.title }}
        </h1>
      </div>
    </header>

    <div class="pdv-grid">
      <main class="pdv-main">
        <nav class="pdv-tabs" role="tablist" aria-label="Pattern detail sections">
          <button type="button" role="tab" :aria-selected="activeTab === 'overview'"
            :class="['pdv-tab', { 'pdv-tab-active': activeTab === 'overview' }, 'focus-visible:outline-2 focus-visible:outline-blue-500']"
            @click="activeTab = 'overview'">Overview</button>
          <button type="button" role="tab" :aria-selected="activeTab === 'content'"
            :class="['pdv-tab', { 'pdv-tab-active': activeTab === 'content' }, 'focus-visible:outline-2 focus-visible:outline-blue-500']"
            @click="activeTab = 'content'">Content</button>
          <button v-if="hasRules" type="button" role="tab" :aria-selected="activeTab === 'rules'"
            :class="['pdv-tab', { 'pdv-tab-active': activeTab === 'rules' }, 'focus-visible:outline-2 focus-visible:outline-blue-500']"
            @click="activeTab = 'rules'">
            Rules <span class="pdv-tab-count">{{ ruleCount }}</span>
          </button>
          <button v-if="hasExperiments" type="button" role="tab" :aria-selected="activeTab === 'experiments'"
            :class="['pdv-tab', { 'pdv-tab-active': activeTab === 'experiments' }, 'focus-visible:outline-2 focus-visible:outline-blue-500']"
            @click="activeTab = 'experiments'">
            Experiments <span v-if="data.experiments?.length" class="pdv-tab-count">{{ data.experiments.length }}</span>
          </button>
        </nav>

        <!-- Overview -->
        <section v-show="activeTab === 'overview'" class="pdv-panel" role="tabpanel">
          <dl class="pdv-meta">
            <div class="pdv-meta-row">
              <dt>Description</dt>
              <dd class="pdv-meta-dd-flow">
                <PatternDescriptionEditor
                  :slug="route.params.slug"
                  :description="data.description || ''"
                  @saved="load" />
              </dd>
            </div>

            <div class="pdv-meta-row">
              <dt>Tags</dt>
              <dd>
                <PatternTagsEditor
                  :slug="route.params.slug"
                  :tags="data.tags || []"
                  :all-tags="data.all_tags || []"
                  @saved="load" />
              </dd>
            </div>

            <div v-if="data.skill_id" class="pdv-meta-row">
              <dt>Skill</dt>
              <dd>
                <router-link :to="`/skills/${data.skill_id}`" class="text-blue-600 hover:underline"><code>{{ data.skill_id }}</code></router-link>
                <Badge v-if="globalSkillStatus" :color="globalSkillStatus.color" :label="globalSkillStatus.label" />
                <Badge v-if="globalSkillStatus?.scope" color="gray" :label="globalSkillStatus.scope" />
              </dd>
            </div>
          </dl>
        </section>

        <!-- Content -->
        <section v-show="activeTab === 'content'" class="pdv-panel" role="tabpanel">
          <PatternContentEditor
            :slug="route.params.slug"
            :body-md="data.body_md || ''"
            :concealed-texts="data.concealed_texts || []"
            @saved="load" />
        </section>

        <!-- Rules -->
        <section v-show="activeTab === 'rules' && hasRules" class="pdv-panel" role="tabpanel">
          <PatternRulesPanel
            :slug="route.params.slug"
            :enforcing-rules="data.enforcing_rules || []"
            :attached-bundles="data.attached_rule_bundles || []"
            :enabled-rule-count="enabledRuleCount"
            :disabled-rule-count="disabledRuleCount"
            @saved="load" />
        </section>

        <!-- Experiments -->
        <section v-show="activeTab === 'experiments' && hasExperiments" class="pdv-panel" role="tabpanel">
          <h2 class="pdv-section-title">Concealment experiments</h2>
          <p class="text-xs text-gray-500 mb-4">
            Hide H2 sections before shipping as a skill to measure behavioral impact.
            <router-link to="/experiments" class="text-blue-600 hover:underline">All experiments</router-link>.
          </p>
          <div v-for="e in data.experiments" :key="e.id" class="border border-gray-200 rounded-lg p-4 mb-3" :class="{ 'bg-emerald-50/50': e.active }">
            <div class="flex items-center gap-3 flex-wrap">
              <router-link :to="`/experiments/${e.id}`" class="font-semibold text-blue-600 hover:underline">{{ e.name }}</router-link>
              <Badge :color="e.active ? 'green' : 'gray'" :label="e.active ? 'active' : 'idle'" />
              <span class="flex-1"></span>
              <button v-if="e.active" type="button" class="btn btn-secondary text-xs focus-visible:outline-2 focus-visible:outline-blue-500" :disabled="expProcessing === e.id" @click="deactivateExp(e.id)">
                {{ expProcessing === e.id ? 'Deactivating...' : 'Deactivate' }}
              </button>
              <button v-else type="button" class="btn btn-primary text-xs focus-visible:outline-2 focus-visible:outline-blue-500" :disabled="expProcessing === e.id" @click="activateExp(e.id)">
                {{ expProcessing === e.id ? 'Activating...' : 'Activate' }}
              </button>
            </div>
            <div class="mt-2 text-sm text-gray-600">
              <span class="text-xs font-medium text-gray-500">Concealed:</span>
              <code v-for="s in e.sections" :key="s" class="text-xs mr-1">{{ s }}</code>
            </div>
          </div>

          <PatternExperimentCreator
            :pattern-slug="data.procedure_id"
            :available-sections="data.available_sections || []"
            @saved="load" />
        </section>
      </main>

      <aside class="pdv-rail" aria-label="Deployment">
        <div class="pdv-rail-head">
          <h2 class="pdv-rail-title">Deployment</h2>
          <p class="pdv-rail-sub">Where this skill currently lives.</p>
        </div>

        <ChannelRow
          v-if="data.skill_id"
          name="Global skill"
          :status="globalSkillStatus"
          :primary="globalSkillPrimary"
          :kebab="globalSkillKebab" />

        <PatternProjectsPicker
          v-if="data.skill_id"
          :skill-id="data.skill_id"
          :repos="repos"
          :deployments="deployments"
          @saved="loadDeployments" />

        <ChannelRow
          v-if="skillhubStatus.available"
          name="regin-skillhub"
          :status="{ color: 'purple', label: 'available' }"
          :primary="skillhubPrimary" />
        <p v-else-if="skillhubStatus.reason && skillhubStatus.reason !== 'loading…'"
          class="pdv-skillhub-offline"
          :title="`regin-skillhub unreachable: ${skillhubStatus.reason}`">
          regin-skillhub offline
        </p>

        <ChannelRow
          v-if="data.enforcing_rules?.length"
          name="Rule enforcement"
          :status="ruleEnforcementStatus"
          :primary="ruleEnforcementPrimary"
          :kebab="ruleEnforcementKebab">
          <p class="pdv-empty-hint">
            Rules run via PostToolUse on edits.
            <button type="button" class="pdv-inline-edit focus-visible:outline-2 focus-visible:outline-blue-500" @click="activeTab = 'rules'">View list</button>
          </p>
        </ChannelRow>

        <div class="pdv-danger-zone">
          <div class="pdv-danger-head">Danger zone</div>
          <button type="button" class="pdv-danger-link focus-visible:outline-2 focus-visible:outline-blue-500"
            @click="deletePattern">Delete pattern</button>
          <p class="pdv-danger-hint">Removes the pattern directory, DB entry, and unlinks any deployed skill.</p>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.pdv-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 1.5rem;
}
@media (min-width: 1024px) {
  .pdv-grid {
    grid-template-columns: minmax(0, 1fr) 380px;
    align-items: flex-start;
  }
}

.pdv-main {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 0.75rem;
  overflow: hidden;
  min-width: 0;
}

.pdv-tabs {
  display: flex;
  gap: 0.25rem;
  padding: 0.5rem 0.5rem 0 0.5rem;
  border-bottom: 1px solid #e5e7eb;
  background: #fafafa;
  overflow-x: auto;
}
.pdv-tab {
  padding: 0.5rem 0.875rem;
  font-size: 0.8125rem;
  font-weight: 500;
  color: #64748b;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 0.375rem 0.375rem 0 0;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  cursor: pointer;
  transition: color 150ms, background-color 150ms, border-color 150ms;
  white-space: nowrap;
}
.pdv-tab:hover { color: #0f172a; background: #f1f5f9; }
.pdv-tab-active {
  color: #1e40af;
  background: #fff;
  border-color: #e5e7eb;
  border-bottom-color: #2563eb;
}
.pdv-tab-count {
  display: inline-block;
  margin-left: 0.25rem;
  padding: 0 0.375rem;
  font-size: 0.6875rem;
  background: #e5e7eb;
  color: #475569;
  border-radius: 999px;
}
.pdv-tab-active .pdv-tab-count { background: #dbeafe; color: #1e40af; }

.pdv-panel {
  padding: 1.25rem 1.5rem 1.5rem 1.5rem;
}

.pdv-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}
.pdv-section-title {
  font-size: 1rem;
  font-weight: 600;
  color: #0f172a;
  margin: 0;
}

/* Overview metadata --------------------------------------------------- */
.pdv-meta {
  display: grid;
  gap: 1rem;
  margin: 0;
}
.pdv-meta-row {
  display: grid;
  grid-template-columns: 80px 1fr;
  gap: 1rem;
  align-items: baseline;
}
.pdv-meta-row dt {
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #94a3b8;
}
.pdv-meta-row dd {
  margin: 0;
  font-size: 0.875rem;
  color: #0f172a;
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
  align-items: center;
}
.pdv-meta-row dd.pdv-meta-dd-flow {
  display: block;
}

/* Right rail ---------------------------------------------------------- */
.pdv-rail {
  background: #fafafa;
  border: 1px solid #e5e7eb;
  border-radius: 0.75rem;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.625rem;
}
.pdv-rail-head { margin-bottom: 0.25rem; }
.pdv-rail-title {
  font-size: 0.9375rem;
  font-weight: 600;
  color: #0f172a;
  margin: 0;
}
.pdv-rail-sub {
  font-size: 0.75rem;
  color: #64748b;
  margin: 0.125rem 0 0 0;
}

.pdv-skillhub-offline {
  font-size: 0.75rem;
  color: #94a3b8;
  margin: 0.25rem 0 0 0.25rem;
  font-style: italic;
}

/* Attached rule bundle header copy */
.pdv-bundle-desc {
  font-size: 0.8125rem;
  color: #475569;
  margin: 0 0 0.5rem 0;
  line-height: 1.55;
}
.pdv-bundle-invocation {
  margin: 0 0 0.875rem 0;
  font-size: 0.75rem;
  color: #64748b;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.pdv-bundle-invocation-label {
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #94a3b8;
  font-size: 0.6875rem;
  flex-shrink: 0;
}
.pdv-bundle-invocation code {
  font-size: 0.75rem;
  padding: 0.125rem 0.4375rem;
  background: #f1f5f9;
  border-radius: 0.25rem;
  color: #1e293b;
  word-break: break-all;
}
.pdv-bundle-rules-head {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #94a3b8;
  margin: 0.5rem 0 0.5rem 0;
  display: flex;
  align-items: center;
  gap: 0.375rem;
}
.pdv-bundle-rules-count {
  display: inline-block;
  padding: 0 0.375rem;
  font-size: 0.6875rem;
  background: #e2e8f0;
  color: #475569;
  border-radius: 999px;
  letter-spacing: 0;
}
.pdv-bundle-rule-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin: 0;
  padding: 0;
}
.pdv-bundle-rule-list .pdv-rule-card {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 0.5rem;
  padding: 0.75rem 1rem;
  gap: 0.375rem;
  transition: border-color 120ms, background 120ms, box-shadow 120ms;
}
.pdv-bundle-rule-list .pdv-rule-card:last-child {
  border-bottom: 1px solid #e5e7eb;
}
.pdv-bundle-rule-list .pdv-rule-card:hover {
  background: #f8fafc;
  border-color: #cbd5e1;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}
.pdv-bundle-rule-list .pdv-rule-head {
  gap: 0.5rem;
}
.pdv-bundle-rule-list .pdv-rule-head code {
  font-size: 0.8125rem;
  font-weight: 600;
  color: #0f172a;
  background: transparent;
  padding: 0;
}
.pdv-bundle-rule-list .pdv-rule-desc {
  color: #64748b;
  font-size: 0.8125rem;
}

/* Per-rule cards inside the Rules tab */
.pdv-rule-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.pdv-rule-card {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  padding: 0.625rem 0;
  border-bottom: 1px solid #f1f5f9;
}
.pdv-rule-card:last-child { border-bottom: 0; }
.pdv-rule-card-disabled { opacity: 0.55; }
.pdv-rule-head {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  flex-wrap: wrap;
}
.pdv-rule-head code {
  font-size: 0.8125rem;
}
.pdv-rule-spacer { flex: 1; }
.pdv-rule-desc {
  font-size: 0.8125rem;
  color: #475569;
  margin: 0;
  line-height: 1.5;
}
.pdv-rule-toggle {
  font-size: 0.75rem;
  padding: 0.125rem 0.5rem;
  background: transparent;
  border: 1px solid #cbd5e1;
  border-radius: 0.25rem;
  color: #475569;
  cursor: pointer;
  white-space: nowrap;
}
.pdv-rule-toggle:hover { background: #f1f5f9; color: #0f172a; }
.pdv-rule-toggle-danger {
  color: #b91c1c;
  border-color: #fecaca;
}
.pdv-rule-toggle-danger:hover { background: #fef2f2; color: #991b1b; }

/* Danger zone */
.pdv-danger-zone {
  margin-top: 0.5rem;
  padding-top: 0.75rem;
  border-top: 1px dashed #e5e7eb;
}
.pdv-danger-head {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #94a3b8;
  margin-bottom: 0.375rem;
}
.pdv-danger-link {
  background: transparent;
  border: 0;
  color: #b91c1c;
  cursor: pointer;
  padding: 0;
  font-size: 0.8125rem;
  font-weight: 500;
}
.pdv-danger-link:hover { text-decoration: underline; }
.pdv-danger-hint {
  font-size: 0.6875rem;
  color: #94a3b8;
  margin: 0.25rem 0 0 0;
}
</style>
