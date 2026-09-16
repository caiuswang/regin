<script setup>
// The index's rows, and the only place the ordering is named twice.
//
// A sortable header is a `button` inside its `th`, and the `th` carries
// `aria-sort` — so the order is announced rather than merely rendered, and a
// column that cannot be sorted wears no affordance at all. The header and the
// toolbar's sort control write the SAME `?sort=key:dir`, because two controls
// over one axis that disagree is how a list ends up in an order neither of
// them claims.
//
// `rounds` is a column. It is the figure this whole pipeline exists to count —
// how many attempts a feature took — and it rode in the payload unrendered.
//
// The whole row is the hit target. `tr:hover` promised one and only the title
// delivered it, which is a click that lands nowhere for everyone who aimed at
// the stage or the cost. The title stays a real anchor, so the keyboard and
// "open in new tab" still work; the row handler is for the pointer, and it
// yields to any link or button inside it rather than swallowing their clicks.
import { useRouter } from 'vue-router'
import Badge from '../Badge.vue'
import Button from '../ui/Button.vue'
import { STAGE_COLOR, money, shortDate } from '../../utils/ledgerFormat'
import { defectsLink, sessionsLink, tasksLink } from '../../utils/featureLinks'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  sortKey: { type: String, default: '' },
  sortDir: { type: String, default: 'desc' },
})
const emit = defineEmits(['sort'])

const router = useRouter()

//: Column → the sort key it orders by. A column absent from this map is plain
//: text: `lib/features/index.py::SORT_KEYS` is the authority for what can be
//: ordered, and offering a control the server would ignore is the silent
//: fallback R3 exists to prevent.
const COLUMNS = [
  { label: 'Feature', key: '', align: '' },
  { label: 'Stage', key: 'stage', align: '' },
  { label: 'Rounds', key: 'rounds', align: 'num' },
  { label: 'Sessions', key: '', align: 'num' },
  { label: 'Tasks', key: '', align: 'num' },
  { label: 'Cost', key: 'cost', align: 'num' },
  { label: 'Defects', key: 'open_defects', align: 'num' },
  { label: 'Last activity', key: 'activity', align: 'num' },
]

function ariaSort(key) {
  if (!key || key !== props.sortKey) return 'none'
  return props.sortDir === 'asc' ? 'ascending' : 'descending'
}

// Clicking the active column toggles direction; clicking another adopts it and
// keeps the direction, so a reader comparing two columns is not also fighting
// a reversal they did not ask for.
function onHeader(key) {
  if (!key) return
  emit('sort', key, key === props.sortKey
    ? (props.sortDir === 'asc' ? 'desc' : 'asc')
    : props.sortDir)
}

function onRow(event, row) {
  if (event.target.closest('a, button')) return
  router.push(`/features/${row.id}`)
}
</script>

<template>
  <table class="index">
    <thead>
      <tr>
        <th
          v-for="column in COLUMNS"
          :key="column.label"
          scope="col"
          :class="column.align"
          :aria-sort="ariaSort(column.key)"
        >
          <Button
            v-if="column.key"
            variant="link"
            size="sm"
            class="sort-header"
            :data-testid="`sort-${column.key}`"
            @click="onHeader(column.key)"
          >
            {{ column.label }}
            <span v-if="column.key === sortKey" class="sort-mark" aria-hidden="true"
            >{{ sortDir === 'asc' ? '↑' : '↓' }}</span>
          </Button>
          <template v-else>{{ column.label }}</template>
        </th>
      </tr>
    </thead>
    <tbody>
      <tr
        v-for="row in rows"
        :key="row.id"
        class="hit"
        data-testid="feature-row"
        @click="onRow($event, row)"
      >
        <td>
          <router-link class="key" :to="`/features/${row.id}`"
          >{{ row.title }}</router-link>
          <!-- The reference, where there is one. Beside the title, never
               in place of it: `features.title` is the authority for what
               is being delivered. -->
          <span v-if="row.external_ref" class="title"
          >{{ row.external_ref }}</span>
        </td>
        <td>
          <!-- Always a real stage: every feature carries its own `specced`
               event, so the "stage not fetched" this cell used to render
               for 31 of them has nothing left to describe. -->
          <Badge
            :color="STAGE_COLOR[row.stage] || 'gray'"
            :label="row.stage"
          />
        </td>
        <td class="num" data-testid="feature-rounds">{{ row.rounds }}</td>
        <td class="num">
          <!-- Through to the nine rows the nine is a claim about. The
               sessions list takes no feature parameter, so the evidence
               list on the feature's own page is where they are. -->
          <router-link
            v-if="row.sessions"
            :to="sessionsLink(row.id)"
            :data-testid="`feature-sessions-link-${row.id}`"
          >{{ row.sessions }}</router-link>
          <span v-else>—</span>
        </td>
        <td class="num">
          <!-- The steps of this feature, on the tab that runs them. The
               edge existed in the data layer and in `FeatureTasks`; the
               index offered no way onto it at all. -->
          <router-link
            v-if="row.tasks"
            :to="tasksLink(row.id)"
            :data-testid="`feature-tasks-link-${row.id}`"
          >{{ row.tasks }}</router-link>
          <span v-else>—</span>
        </td>
        <td class="num" :class="row.cost_usd === null ? 'is-empty' : ''">
          <template v-if="row.cost_usd === null">none declared</template>
          <template v-else>≤ {{ money(row.cost_usd) }}</template>
        </td>
        <td class="num">
          <!-- A link, because the count is the question and the rows are
               the answer: a number with no way through to what it counts
               is where a reported defect goes unread. -->
          <router-link
            v-if="row.defects.total"
            :to="defectsLink(row.id)"
            :class="row.defects.escapes ? 'text-amber-700' : ''"
          >{{ row.defects.open }} / {{ row.defects.total }}</router-link>
          <span v-else>{{ row.defects.open }} / {{ row.defects.total }}</span>
        </td>
        <td class="num muted">{{ shortDate(row.last_activity) }}</td>
      </tr>
    </tbody>
  </table>
</template>

<style scoped>
.index { width: 100%; border-collapse: collapse; font-size: 13px; }
.index th {
    text-align: left; font-size: 10.5px; font-weight: 600;
    letter-spacing: 0.06em; text-transform: uppercase;
    color: var(--color-fg-faint); padding: 0.3rem 0.5rem;
    border-bottom: 1px solid var(--color-border);
}
.index th.num { text-align: right; }
.index td {
    padding: 0.45rem 0.5rem;
    border-bottom: 1px solid var(--color-border-subtle);
    vertical-align: baseline;
}
.index tbody tr:hover { background: var(--color-slate-50); }
.index .num {
    text-align: right; font-variant-numeric: tabular-nums;
    white-space: nowrap;
}
.index .muted { color: var(--color-fg-faint); }
.hit { cursor: pointer; }
.sort-header {
    font: inherit; letter-spacing: inherit; text-transform: inherit;
    color: inherit; text-decoration: none; min-height: 1.5rem;
}
.sort-header:hover { color: var(--color-fg); text-decoration: none; }
.sort-header:focus-visible { outline: 2px solid var(--color-blue-500); }
.index th.num .sort-header { flex-direction: row-reverse; }
.sort-mark { font-size: 11px; }
.key {
    font-family: var(--font-mono, monospace); font-weight: 600;
    color: var(--color-primary); text-decoration: none;
}
.key:hover { text-decoration: underline; }
.key:focus-visible { outline: 2px solid var(--color-blue-500); }
.title {
    display: block; max-width: 34rem; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap;
    font-size: 11.5px; color: var(--color-fg-subtle);
}
.is-empty { font-style: italic; color: var(--color-fg-faint); }
@media (max-width: 768px) {
    .index { display: block; overflow-x: auto; }
}
</style>
