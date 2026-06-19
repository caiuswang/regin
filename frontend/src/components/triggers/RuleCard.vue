<script setup>
import { computed } from 'vue'
import RuleSpark from './RuleSpark.vue'
import RuleDrawer from './RuleDrawer.vue'

const props = defineProps({
  rule: { type: Object, required: true },
  expanded: { type: Boolean, default: false },
  range: { type: String, default: '7d' },
})
const emit = defineEmits(['toggle', 'suppression-changed'])

const statusBadge = computed(() => ({
  noisy:  { color: 'yellow', label: 'noisy' },
  active: { color: 'green',  label: 'active' },
  dead:   { color: 'gray',   label: 'dead' },
}[props.rule.status] || { color: 'gray', label: props.rule.status }))

// CSS vars so the inline :style color follows the active theme.
const rateColor = computed(() => ({
  noisy: 'var(--color-amber-700)',
  active: 'var(--color-teal-700)',
  dead: 'var(--color-slate-400)',
}[props.rule.status] || 'var(--color-teal-700)'))

const isDead = computed(() => props.rule.status === 'dead')
const isNoisy = computed(() => props.rule.status === 'noisy')

function onToggle() { emit('toggle') }
</script>

<template>
  <article
    class="rule-card"
    :class="{ 'rule-card--noisy': isNoisy, 'rule-card--dead': isDead, 'rule-card--open': expanded }"
  >
    <!-- Accent stripe (noisy only) -->
    <div v-if="isNoisy" class="rule-card__accent" aria-hidden="true" />

    <!-- Header (click to toggle) -->
    <button
      type="button"
      class="rule-card__header focus-visible:outline-2 focus-visible:outline-blue-500"
      :aria-expanded="expanded"
      @click="onToggle"
    >
      <span class="rule-card__caret" aria-hidden="true">{{ expanded ? '▾' : '▸' }}</span>
      <code class="rule-card__id">{{ rule.rule_id }}</code>
      <span v-if="rule.severity" :class="`badge badge-${rule.severity === 'error' ? 'red' : rule.severity === 'warn' ? 'yellow' : 'blue'}`">{{ rule.severity }}</span>
      <span v-if="rule.source" class="badge badge-gray">{{ rule.source }}</span>
      <router-link
        v-if="rule.experiment_id"
        :to="`/experiments/${rule.experiment_id}`"
        class="badge badge-blue no-underline"
        @click.stop
      >
        experiment #{{ rule.experiment_id }}
      </router-link>
      <span class="flex-1" />
      <span v-if="rule.last_seen" class="rule-card__last-seen" :title="rule.last_seen">
        {{ rule.last_seen }}
      </span>
      <span :class="`badge badge-${statusBadge.color}`">{{ statusBadge.label }}</span>
    </button>

    <!-- Metrics row -->
    <div class="rule-card__metrics">
      <RuleSpark :buckets="rule.spark" :status="rule.status" :width="200" :height="28" />
      <div class="rule-card__metric-text">
        <span class="rule-card__rate" :style="{ color: rateColor }">
          {{ rule.trigger_rate_pct }}%
        </span>
        <span class="rule-card__rate-label">trigger rate</span>
        <span class="rule-card__counts">
          {{ rule.fires }} fires / {{ rule.checks }} checks
        </span>
        <span v-if="rule.suppressed_count > 0"
              class="rule-card__suppressed-hint"
              :title="`${rule.suppressed_count} event(s) flagged as noise are excluded from the metrics above.`">
          · {{ rule.suppressed_count }} suppressed
        </span>
      </div>
    </div>

    <!-- Guide preview -->
    <p v-if="rule.guide_preview" class="rule-card__guide">
      "{{ rule.guide_preview }}"
    </p>

    <!-- Top files OR dead-CTA -->
    <div v-if="isDead" class="rule-card__dead-cta">
      ↑ never fired across {{ rule.checks }} checks —
      <router-link
        :to="`/rules/${encodeURIComponent(rule.rule_id)}`"
        class="rule-card__cta-link"
        @click.stop
      >open rule editor →</router-link>
    </div>
    <p v-else-if="rule.top_files && rule.top_files.length" class="rule-card__top-files">
      top:
      <span v-for="(f, i) in rule.top_files" :key="f.name">
        <code class="rule-card__file">{{ f.name }}</code>
        <span class="rule-card__file-count">({{ f.n }})</span>
        <span v-if="i < rule.top_files.length - 1"> · </span>
      </span>
    </p>

    <!-- Lazy drawer -->
    <RuleDrawer
      v-if="expanded"
      :rule-id="rule.rule_id"
      :range="range"
      :fallback-severity="rule.severity"
      :fallback-source="rule.source"
      @suppression-changed="$emit('suppression-changed')"
    />
  </article>
</template>

<style scoped>
.rule-card {
  position: relative;
  background: var(--color-white);
  border: 1px solid var(--color-slate-200);
  border-radius: 8px;
  padding: 14px 18px;
  margin-bottom: 12px;
  transition: border-color 150ms;
}
.rule-card--open {
  border-color: var(--color-blue-300);
  box-shadow: 0 4px 12px rgba(30, 64, 175, 0.06);
}
.rule-card--dead { opacity: 0.7; }
.rule-card--noisy { padding-left: 22px; }

.rule-card__accent {
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 4px;
  background: var(--color-amber-400);
  border-top-left-radius: 8px;
  border-bottom-left-radius: 8px;
}

.rule-card__header {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  background: transparent;
  border: 0;
  padding: 0;
  margin: 0 0 10px 0;
  cursor: pointer;
  text-align: left;
}
.rule-card__caret {
  color: var(--color-slate-500);
  font-size: 14px;
  width: 14px;
  flex-shrink: 0;
}
.rule-card__id {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-slate-800);
}
.rule-card__last-seen {
  font-size: 11px;
  color: var(--color-slate-400);
  font-variant-numeric: tabular-nums;
}

.rule-card__metrics {
  display: flex;
  align-items: center;
  gap: 20px;
  margin-bottom: 8px;
}
.rule-card__metric-text {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.rule-card__rate {
  font-size: 26px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.rule-card__rate-label {
  font-size: 11px;
  color: var(--color-slate-400);
}
.rule-card__counts {
  font-size: 12px;
  color: var(--color-slate-600);
  font-variant-numeric: tabular-nums;
  margin-left: 6px;
}
.rule-card__suppressed-hint {
  font-size: 11px;
  color: var(--color-slate-400);
  margin-left: 4px;
  cursor: help;
}

.rule-card__guide {
  font-size: 13px;
  color: var(--color-slate-500);
  font-style: italic;
  margin: 0 0 6px 0;
}

.rule-card__top-files {
  font-size: 11px;
  color: var(--color-slate-400);
  margin: 0;
}
.rule-card__file {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--color-slate-600);
}
.rule-card__file-count { margin-left: 2px; }

.rule-card__dead-cta {
  font-size: 11px;
  color: var(--color-slate-400);
}
.rule-card__cta-link {
  color: var(--color-blue-800);
  text-decoration: none;
}
.rule-card__cta-link:hover { text-decoration: underline; }

.no-underline { text-decoration: none; }
</style>
