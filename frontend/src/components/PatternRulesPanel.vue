<script setup>
import api from '../api'
import Badge from './Badge.vue'
import Button from './ui/Button.vue'
import { useFlash } from '../composables/useFlash'
import { useConfirm } from '../composables/useConfirm'

// Extracted from PatternDetailView (PR 2.4g). Owns the rules tab body:
// the single-rule list (enforcing_rules) + the attached bundle list,
// plus the five toggle helpers (disable/enable all, single-rule toggle,
// bundle-single toggle, bundle-all toggle). The parent keeps the
// wrapping `<section v-show="activeTab === 'rules' && hasRules">`
// because it gates on parent-owned activeTab + hasRules.
const props = defineProps({
  slug: { type: String, required: true },
  enforcingRules: { type: Array, default: () => [] },
  attachedBundles: { type: Array, default: () => [] },
  enabledRuleCount: { type: Number, default: 0 },
  disabledRuleCount: { type: Number, default: 0 },
})
const emit = defineEmits(['saved'])

const { flash } = useFlash()
const { confirm } = useConfirm()

async function disableRules() {
  const ok = await confirm(
    'Disable rules',
    `Disable ${props.enabledRuleCount} rule(s)?`,
    true,
  )
  if (!ok) return
  const result = await api.post(`/patterns/${props.slug}/rules/disable`)
  if (!result.ok) {
    flash(result.msg || 'Failed to disable rules', 'error')
    return
  }
  flash(result.msg)
  emit('saved')
}

async function enableRules() {
  const result = await api.post(`/patterns/${props.slug}/rules/enable`)
  if (!result.ok) {
    flash(result.msg || 'Failed to enable rules', 'error')
    return
  }
  flash(result.msg)
  emit('saved')
}

async function toggleSingleRule(ruleId, disabled) {
  const action = disabled ? 'disable' : 'enable'
  const result = await api.post(
    `/patterns/${props.slug}/rules/${action}`,
    { rule_ids: [ruleId] },
  )
  if (!result.ok) {
    flash(result.msg || `Failed to ${action} rule`, 'error')
    return
  }
  flash(result.msg)
  emit('saved')
}

async function toggleBundleRule(engineId, ruleId, disabled) {
  const action = disabled ? 'disable' : 'enable'
  const result = await api.post(
    `/patterns/${props.slug}/bundle-rules/${action}`,
    { engine_id: engineId, rule_ids: [ruleId] },
  )
  if (!result.ok) {
    flash(result.msg || `Failed to ${action} rule`, 'error')
    return
  }
  flash(result.msg)
  emit('saved')
}

async function toggleBundleAll(engineId, ruleIds, disabled) {
  if (!ruleIds.length) return
  const action = disabled ? 'disable' : 'enable'
  const noun = ruleIds.length === 1 ? 'rule' : 'rules'
  if (disabled) {
    const ok = await confirm(
      'Disable bundle rules',
      `Disable ${ruleIds.length} ${engineId} ${noun}?`,
      true,
    )
    if (!ok) return
  }
  const result = await api.post(
    `/patterns/${props.slug}/bundle-rules/${action}`,
    { engine_id: engineId, rule_ids: ruleIds },
  )
  if (!result.ok) {
    flash(result.msg || `Failed to ${action}`, 'error')
    return
  }
  flash(result.msg)
  emit('saved')
}

function bundleEnabledIds(bundle) {
  return bundle.rules.filter((r) => !r.disabled).map((r) => r.id)
}

function bundleDisabledIds(bundle) {
  return bundle.rules.filter((r) => r.disabled).map((r) => r.id)
}
</script>

<template>
  <div v-if="enforcingRules.length" class="mb-6">
    <h2 class="pdv-section-title">
      Enforced by {{ enforcingRules.length }} rule{{ enforcingRules.length !== 1 ? 's' : '' }}
      <Badge v-if="disabledRuleCount" color="gray" :label="`${disabledRuleCount} disabled`" class="ml-1" />
    </h2>
    <p class="text-xs text-gray-500 mb-3">
      Rules run via the PostToolUse hook on edits in languages registered with a rule engine.
      Disabling skips them; engine source stays.
    </p>
    <ul class="pdv-rule-list mb-4">
      <li
        v-for="r in enforcingRules"
        :key="r.id"
        class="pdv-rule-card"
        :class="{ 'pdv-rule-card-disabled': r.disabled }">
        <div class="pdv-rule-head">
          <router-link :to="`/rules/${r.id}`" class="text-blue-600 hover:underline"><code>{{ r.id }}</code></router-link>
          <Badge v-if="r.severity === 'error'" color="red" label="error" />
          <Badge v-else-if="r.severity === 'warn'" color="yellow" label="warn" />
          <Badge v-if="r.disabled" color="gray" label="disabled" />
          <span class="pdv-rule-spacer"></span>
          <button
            type="button"
            class="pdv-rule-toggle focus-visible:outline-2 focus-visible:outline-blue-500"
            :class="{ 'pdv-rule-toggle-danger': !r.disabled }"
            @click="toggleSingleRule(r.id, !r.disabled)">
            {{ r.disabled ? 'Enable' : 'Disable' }}
          </button>
        </div>
        <p class="pdv-rule-desc">{{ r.summary }}</p>
      </li>
    </ul>
    <div class="btn-row">
      <Button
        v-if="enabledRuleCount"
        variant="danger"
        size="sm"
        @click="disableRules">
        Disable {{ enabledRuleCount }} rule{{ enabledRuleCount !== 1 ? 's' : '' }}
      </Button>
      <Button
        v-if="disabledRuleCount"
        variant="secondary"
        size="sm"
        @click="enableRules">
        Re-enable {{ disabledRuleCount }}
      </Button>
    </div>
  </div>

  <div v-if="attachedBundles.length">
    <h2 class="pdv-section-title">Attached rule bundles</h2>
    <div
      v-for="bundle in attachedBundles"
      :key="bundle.engine_id"
      class="mb-5 last:mb-0">
      <div class="flex items-center gap-2 flex-wrap mb-2">
        <h3 class="text-sm font-semibold text-slate-800">{{ bundle.title }}</h3>
        <Badge color="blue" :label="bundle.engine_id" />
        <span class="text-xs text-gray-500">{{ bundle.rules.length }} rule{{ bundle.rules.length !== 1 ? 's' : '' }}</span>
        <span class="flex-1"></span>
        <Button
          v-if="bundleEnabledIds(bundle).length"
          variant="danger"
          size="sm"
          @click="toggleBundleAll(bundle.engine_id, bundleEnabledIds(bundle), true)">
          Disable {{ bundleEnabledIds(bundle).length }}
        </Button>
        <Button
          v-if="bundleDisabledIds(bundle).length"
          variant="secondary"
          size="sm"
          @click="toggleBundleAll(bundle.engine_id, bundleDisabledIds(bundle), false)">
          Re-enable {{ bundleDisabledIds(bundle).length }}
        </Button>
      </div>
      <p class="pdv-bundle-desc">{{ bundle.description }}</p>
      <p class="pdv-bundle-invocation">
        <span class="pdv-bundle-invocation-label">Run with</span>
        <code>{{ bundle.invocation_hint }}</code>
      </p>
      <h4 class="pdv-bundle-rules-head">
        Rules <span class="pdv-bundle-rules-count">{{ bundle.rules.length }}</span>
      </h4>
      <ul class="pdv-rule-list pdv-bundle-rule-list">
        <li
          v-for="r in bundle.rules"
          :key="`${bundle.engine_id}:${r.id}`"
          class="pdv-rule-card"
          :class="{ 'pdv-rule-card-disabled': r.disabled }">
          <div class="pdv-rule-head">
            <code>{{ r.id }}</code>
            <Badge v-if="r.severity === 'error'" color="red" label="error" />
            <Badge v-else-if="r.severity === 'warn'" color="yellow" label="warn" />
            <Badge v-if="r.checker" color="gray" :label="r.checker" />
            <Badge v-if="r.disabled" color="gray" label="disabled" />
            <span class="pdv-rule-spacer"></span>
            <button
              type="button"
              class="pdv-rule-toggle focus-visible:outline-2 focus-visible:outline-blue-500"
              :class="{ 'pdv-rule-toggle-danger': !r.disabled }"
              @click="toggleBundleRule(bundle.engine_id, r.id, !r.disabled)">
              {{ r.disabled ? 'Enable' : 'Disable' }}
            </button>
          </div>
          <p class="pdv-rule-desc">{{ r.summary }}</p>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
/* These styles moved here when the rules tab was extracted from
   PatternDetailView. The parent's copies are `scoped`, so they did not
   reach this child component's elements (the per-row toggle buttons were
   rendering as unstyled text). pdv-section-title is also defined in the
   parent because the parent template uses it too. */
.pdv-section-title {
  font-size: 1rem;
  font-weight: 600;
  color: var(--color-slate-900);
  margin: 0;
}

/* Attached rule bundle header copy */
.pdv-bundle-desc {
  font-size: 0.8125rem;
  color: var(--color-slate-600);
  margin: 0 0 0.5rem 0;
  line-height: 1.55;
}
.pdv-bundle-invocation {
  margin: 0 0 0.875rem 0;
  font-size: 0.75rem;
  color: var(--color-slate-500);
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.pdv-bundle-invocation-label {
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-slate-400);
  font-size: 0.6875rem;
  flex-shrink: 0;
}
.pdv-bundle-invocation code {
  font-size: 0.75rem;
  padding: 0.125rem 0.4375rem;
  background: var(--color-slate-100);
  border-radius: 0.25rem;
  color: var(--color-slate-800);
  word-break: break-all;
}
.pdv-bundle-rules-head {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-slate-400);
  margin: 0.5rem 0 0.5rem 0;
  display: flex;
  align-items: center;
  gap: 0.375rem;
}
.pdv-bundle-rules-count {
  display: inline-block;
  padding: 0 0.375rem;
  font-size: 0.6875rem;
  background: var(--color-slate-200);
  color: var(--color-slate-600);
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
  background: var(--color-white);
  border: 1px solid var(--color-gray-200);
  border-radius: 0.5rem;
  padding: 0.75rem 1rem;
  gap: 0.375rem;
  transition: border-color 120ms, background 120ms, box-shadow 120ms;
}
.pdv-bundle-rule-list .pdv-rule-card:last-child {
  border-bottom: 1px solid var(--color-gray-200);
}
.pdv-bundle-rule-list .pdv-rule-card:hover {
  background: var(--color-slate-50);
  border-color: var(--color-slate-300);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}
.pdv-bundle-rule-list .pdv-rule-head {
  gap: 0.5rem;
}
.pdv-bundle-rule-list .pdv-rule-head code {
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--color-slate-900);
  background: transparent;
  padding: 0;
}
.pdv-bundle-rule-list .pdv-rule-desc {
  color: var(--color-slate-500);
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
  border-bottom: 1px solid var(--color-slate-100);
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
  color: var(--color-slate-600);
  margin: 0;
  line-height: 1.5;
}
.pdv-rule-toggle {
  font-size: 0.75rem;
  padding: 0.125rem 0.5rem;
  background: transparent;
  border: 1px solid var(--color-slate-300);
  border-radius: 0.25rem;
  color: var(--color-slate-600);
  cursor: pointer;
  white-space: nowrap;
}
.pdv-rule-toggle:hover { background: var(--color-slate-100); color: var(--color-slate-900); }
.pdv-rule-toggle-danger {
  color: var(--color-red-700);
  border-color: var(--color-red-200);
}
.pdv-rule-toggle-danger:hover { background: var(--color-red-50); color: var(--color-red-800); }
</style>
