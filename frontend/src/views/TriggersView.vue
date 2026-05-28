<script setup>
import { ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import RuleCard from '../components/triggers/RuleCard.vue'
import { useTriggerRules } from '../composables/useTriggerRules'
import { useStickyHeader } from '../composables/useStickyHeader'

const route = useRoute()
const router = useRouter()

const {
  kpis, rules, thresholds, loading, error,
  filters, setFilter, clearFilters, load,
} = useTriggerRules(route, router)

const { stickyHeaderEl, stickyHeaderHeight } = useStickyHeader(loading)

const expandedId = ref(null)
function toggle(ruleId) {
  expandedId.value = expandedId.value === ruleId ? null : ruleId
}

const ranges = ['24h', '7d', '30d', 'all']
const statuses = [
  { value: 'all',    label: 'All',
    hint: 'every rule, regardless of classification' },
  { value: 'active', label: 'Active',
    hint: 'fired at least once but below noisy threshold' },
  { value: 'noisy',  label: 'Noisy',
    hint: 'high trigger rate AND ≥ min fires — classification, not user-flagged' },
  { value: 'dead',   label: 'Dead',
    hint: 'enough checks but 0 fires in range — consider scoping or removing' },
]
const sorts = [
  { value: 'rate',      label: 'Trigger rate' },
  { value: 'fires',     label: 'Fires' },
  { value: 'last_seen', label: 'Last seen' },
  { value: 'rule_id',   label: 'Rule ID' },
]

// Empty-state mode. Distinguishes the four root causes so the message
// can point at a fix instead of just saying "nothing here".
const emptyMode = computed(() => {
  if (loading.value || error.value) return null
  if (rules.value.length > 0) return null
  // Did the user filter the list to zero?
  const f = filters.value
  if (f.search || f.severity || f.engine || f.status !== 'all') return 'filtered'
  // Engines configured but no rules in the catalog?
  if (kpis.value.configured === 0) return 'no-engines-or-rules'
  // Rules exist but nothing fired in the window — typically just "agent stayed clean".
  if (kpis.value.active === 0 && kpis.value.noisy === 0) return 'window-quiet'
  return 'empty'
})
</script>

<template>
  <div v-if="loading && !rules.length" class="empty-state">Loading rule triggers…</div>
  <div v-else-if="error" class="empty-state text-red-700">⚠ {{ error }}</div>
  <div
    v-else
    class="sticky-page-root"
    :style="{ '--regin-trace-header-h': stickyHeaderHeight ? stickyHeaderHeight + 'px' : '0px' }"
  >
    <!-- Sticky toolbar -->
    <div
      ref="stickyHeaderEl"
      class="sticky -top-4 lg:-top-6 z-20 bg-white -mx-4 -mt-4 px-4 pt-4 lg:-mx-8 lg:-mt-6 lg:px-8 lg:pt-6 pb-3 mb-4 border-b border-slate-200 shadow-[0_2px_4px_-2px_rgba(15,23,42,0.06)]"
    >
      <!-- KPI strip -->
      <div class="kpi-strip">
        <div class="kpi-tile">
          <div class="kpi-tile__value">{{ kpis.configured }}</div>
          <div class="kpi-tile__label">rules configured</div>
        </div>
        <div class="kpi-tile kpi-tile--active">
          <div class="kpi-tile__value">{{ kpis.active }}</div>
          <div class="kpi-tile__label">active in window</div>
        </div>
        <div class="kpi-tile kpi-tile--noisy">
          <div class="kpi-tile__value">{{ kpis.noisy }}</div>
          <div class="kpi-tile__label">
            noisy
            <span v-if="thresholds" class="kpi-tile__hint">
              (≥{{ thresholds.noisy_min_rate_pct }}% &amp; ≥{{ thresholds.noisy_min_fires }} fires)
            </span>
          </div>
        </div>
        <div class="kpi-tile kpi-tile--dead">
          <div class="kpi-tile__value">{{ kpis.dead }}</div>
          <div class="kpi-tile__label">
            dead
            <span v-if="thresholds" class="kpi-tile__hint">
              (≥{{ thresholds.dead_min_checks }} checks, 0 fires)
            </span>
          </div>
        </div>
      </div>

      <!-- Toolbar -->
      <div class="trigger-toolbar">
        <input
          type="search"
          class="trigger-toolbar__search focus-visible:outline-2 focus-visible:outline-blue-500"
          placeholder="🔎 search rule_id…"
          :value="filters.search"
          @input="setFilter('search', $event.target.value)"
        />
        <label class="trigger-toolbar__field">
          <span class="trigger-toolbar__field-label">Range</span>
          <select class="focus-visible:outline-2 focus-visible:outline-blue-500"
                  :value="filters.range" @change="setFilter('range', $event.target.value)">
            <option v-for="r in ranges" :key="r" :value="r">{{ r }}</option>
          </select>
        </label>
        <label class="trigger-toolbar__field">
          <span class="trigger-toolbar__field-label">Sort</span>
          <select class="focus-visible:outline-2 focus-visible:outline-blue-500"
                  :value="filters.sort" @change="setFilter('sort', $event.target.value)">
            <option v-for="s in sorts" :key="s.value" :value="s.value">{{ s.label }}</option>
          </select>
        </label>
        <router-link to="/trace/triggers/raw"
                     class="trigger-toolbar__raw-link focus-visible:outline-2 focus-visible:outline-blue-500">
          Raw events log →
        </router-link>
      </div>

      <!-- Status chip row -->
      <div class="trigger-chips">
        <button
          v-for="s in statuses" :key="s.value"
          type="button"
          class="filter-chip focus-visible:outline-2 focus-visible:outline-blue-500"
          :class="{ active: filters.status === s.value }"
          :title="s.hint"
          @click="setFilter('status', s.value === 'all' ? null : s.value)"
        >{{ s.label }}</button>
        <span class="trigger-chips__divider" aria-hidden="true">·</span>
        <!-- Separate filter from the status chips. Status is the
             rate-based classification; "noise marks" is the per-event
             user-flagged set. They can be combined freely. -->
        <button
          type="button"
          class="filter-chip focus-visible:outline-2 focus-visible:outline-blue-500"
          :class="{ active: filters.marks === '1' }"
          title="Rules with at least one user-marked noise event in this range"
          @click="setFilter('marks', filters.marks === '1' ? null : '1')"
        >🔇 With noise marks</button>
        <button
          v-if="filters.search || filters.severity || filters.engine || filters.status !== 'all' || filters.marks"
          type="button"
          class="filter-chip filter-chip--clear focus-visible:outline-2 focus-visible:outline-blue-500"
          @click="clearFilters"
        >Clear filters</button>
      </div>
    </div>

    <!-- Rule list -->
    <div v-if="rules.length" class="rule-list">
      <RuleCard
        v-for="rule in rules"
        :key="rule.rule_id"
        :rule="rule"
        :expanded="expandedId === rule.rule_id"
        :range="filters.range"
        @toggle="toggle(rule.rule_id)"
        @suppression-changed="load"
      />
    </div>

    <!-- Empty states -->
    <div v-else-if="emptyMode === 'filtered'" class="empty-state empty-state--card">
      No rules match the current filters.
      <button type="button" class="empty-state__link focus-visible:outline-2 focus-visible:outline-blue-500" @click="clearFilters">Clear filters</button>
    </div>
    <div v-else-if="emptyMode === 'no-engines-or-rules'" class="empty-state empty-state--card">
      No rule engines have rules indexed.<br />
      Configure one in
      <router-link to="/settings" class="empty-state__link focus-visible:outline-2 focus-visible:outline-blue-500">Settings → Rule engines</router-link>,
      then run <code>regin doctor</code> to verify discovery.
    </div>
    <div v-else-if="emptyMode === 'window-quiet'" class="empty-state empty-state--card">
      No rule fired in the last {{ filters.range }}.<br />
      The agent stayed clean — or the rules don't apply to recent edits.
      Widen the range with the toolbar above.
    </div>
    <div v-else class="empty-state empty-state--card">
      No rule triggers recorded yet.
    </div>
  </div>
</template>

<style scoped>
.kpi-strip {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 12px;
}
.kpi-tile {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 14px 16px;
}
.kpi-tile--active { background: #ecfdf5; border-color: #a7f3d0; }
.kpi-tile--noisy  { background: #fffbeb; border-color: #fde68a; }
.kpi-tile--dead   { background: #f1f5f9; border-color: #cbd5e1; }
.kpi-tile__value {
  font-size: 28px;
  font-weight: 700;
  color: #1e293b;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}
.kpi-tile--active .kpi-tile__value { color: #065f46; }
.kpi-tile--noisy  .kpi-tile__value { color: #b45309; }
.kpi-tile--dead   .kpi-tile__value { color: #475569; }
.kpi-tile__label {
  font-size: 12px;
  color: #64748b;
  margin-top: 2px;
}
.kpi-tile__hint {
  display: block;
  font-size: 10px;
  color: #94a3b8;
  margin-top: 1px;
}

.trigger-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}
.trigger-toolbar__search {
  flex: 0 1 260px;
  min-width: 200px;
  padding: 6px 10px;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  font-size: 12px;
  background: #ffffff;
}
.trigger-toolbar__field {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #475569;
}
.trigger-toolbar__field-label { font-size: 11px; color: #94a3b8; }
.trigger-toolbar__field select {
  padding: 4px 6px;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  background: #ffffff;
  font-size: 12px;
}
.trigger-toolbar__raw-link {
  margin-left: auto;
  font-size: 12px;
  color: #1e40af;
  text-decoration: none;
}
.trigger-toolbar__raw-link:hover { text-decoration: underline; }

.trigger-chips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
}
.filter-chip--clear { color: #94a3b8; }
.trigger-chips__divider { color: #cbd5e1; margin: 0 4px; }

.rule-list { display: block; }

.empty-state--card {
  background: #ffffff;
  border: 1px dashed #cbd5e1;
  border-radius: 8px;
  padding: 32px 24px;
  text-align: center;
  color: #64748b;
  font-size: 13px;
  line-height: 1.6;
}
.empty-state__link {
  background: transparent;
  border: 0;
  color: #1e40af;
  cursor: pointer;
  text-decoration: underline;
  padding: 0;
  margin-left: 4px;
  font: inherit;
}
</style>
