<script setup>
// The session list — the *evidence* for an upper bound, not decoration.
// "$41.90 across 9 sessions" is unfalsifiable without the nine rows.
//
// Row identity is the CLI's: date · trace_id[:8] · title. The 8-char prefix is
// ported verbatim, because it is what lets an id carry by eye between
// `regin stats sessions`, the CLI ledger and this page. The 44-char title
// truncation is not — that was a fixed-width-terminal concession, and CSS
// truncates at the column's real width instead.
//
// When gaps outnumber the rows they qualify, each becomes a **ghost row**:
// hatched, sized like the sessions it is missing, occupying list real estate
// rather than shrinking to `· 5` in grey. A gap must never be smaller than the
// thing it qualifies. Below that threshold it stays a footer line, because
// four ghost rows above two real ones would make the page look like it is
// mostly absence.
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import { gapList, gapTotal, money } from '../../utils/ledgerFormat'
import { SESSIONS_ANCHOR } from '../../utils/featureLinks'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  gaps: { type: Object, default: () => ({}) },
  // The trace route is admin-only. A non-admin still sees every row —
  // omitting one would make a withheld session indistinguishable from a gap,
  // and the cost above is a sum over exactly these rows.
  canOpenTrace: { type: Boolean, default: true },
  canUndeclare: { type: Boolean, default: true },
})
defineEmits(['undeclare'])

const gaps = computed(() => gapList(props.gaps))
const ghosts = computed(() => gapTotal(props.gaps) > props.rows.length)
</script>

<template>
  <!-- The address the sessions figure links to. A count is a claim about rows,
       and a claim with no way through to the rows is the one-way street this
       id closes; the sessions LIST cannot be scoped to a feature by URL, so
       this page is where "9 sessions" resolves. -->
  <div :id="SESSIONS_ANCHOR" class="ev">
    <div v-if="!rows.length" class="ev-empty" data-testid="none-declared">
      <strong>none declared</strong>
      <p>
        No session has been declared as building this feature. That is not
        <span class="ev-zero">$0.00</span> — regin has nothing to add up.
        Attribution is declared, never inferred.
      </p>
    </div>

    <ul v-else class="ev-list">
      <li v-for="r in rows" :key="r.trace_id" class="ev-row">
        <span class="ev-date">{{ r.date }}</span>
        <component
          :is="canOpenTrace ? 'router-link' : 'span'"
          :to="canOpenTrace ? `/trace/sessions/${r.trace_id}` : undefined"
          class="ev-id"
          :class="canOpenTrace ? 'ev-id-link' : ''"
          :title="r.trace_id"
        >{{ r.short_trace }}</component>
        <span class="ev-title" :title="r.title">{{ r.title || '—' }}</span>

        <span v-if="!r.is_primary" class="ev-tag ev-tag-shared">shared</span>
        <!-- The tag is the reason this session is counted, so a `run` one
             that names no task is a provenance nobody can audit. -->
        <router-link
          v-else-if="r.provenance === 'run' && r.task_id"
          class="ev-tag ev-tag-link"
          :to="`/tasks/${r.task_id}`"
        >task #{{ r.task_id }}</router-link>
        <span v-else-if="r.provenance !== 'declared'" class="ev-tag">{{ r.provenance }}</span>
        <span v-if="r.round" class="ev-tag">round {{ r.round }}</span>

        <span
          class="ev-cost"
          :class="r.cost_usd === null ? 'is-gap' : (r.cost_usd === 0 ? 'is-elsewhere' : '')"
        >
          <template v-if="r.cost_usd === null">no usage</template>
          <template v-else-if="r.cost_usd === 0">charged elsewhere</template>
          <template v-else>{{ money(r.cost_usd) }}</template>
        </span>

        <!-- Absent, never disabled, on a task-lane row: there is no row to
             delete, and a greyed button would claim the act is
             possible-but-not-now. The tag beside it points at the task where
             the assertion can actually be withdrawn. -->
        <Button
          v-if="canUndeclare && r.provenance !== 'run'"
          class="ev-undeclare"
          variant="ghost" size="sm"
          :aria-label="`Un-declare ${r.short_trace} from this feature`"
          :title="`Un-declare ${r.short_trace}`"
          @click="$emit('undeclare', r)"
        ><Icon name="trash" :size="12" /></Button>
      </li>

      <template v-if="ghosts">
        <li v-for="g in gaps" :key="g.name" class="ev-ghost">
          <span class="ev-date">—</span>
          <span class="ev-id">×{{ g.count }}</span>
          <span class="ev-title">{{ g.label }} — {{ g.blurb }}</span>
          <span class="ev-cost is-gap">not counted</span>
        </li>
      </template>
    </ul>

    <p v-if="!ghosts && gaps.length" class="ev-gaps">
      <Icon name="alert-triangle" :size="12" />
      <span v-for="g in gaps" :key="g.name" :title="g.blurb">
        <b>{{ g.count }}</b> {{ g.label }}
      </span>
    </p>
  </div>
</template>

<style scoped>
.ev-list { display: flex; flex-direction: column; }
.ev-row, .ev-ghost {
    display: flex; align-items: center; gap: 0.65rem;
    padding: 0.4rem 0.15rem;
    border-bottom: 1px solid var(--color-border-subtle);
    font-size: 13px;
}
.ev-row:hover { background: var(--color-slate-50); }
.ev-ghost {
    color: var(--color-fg-faint);
    background: repeating-linear-gradient(
        135deg, transparent 0 6px, var(--color-slate-50) 6px 12px);
}
.ev-date {
    width: 5.5rem; flex: none; font-family: var(--font-mono, monospace);
    font-size: 11px; color: var(--color-fg-faint);
    font-variant-numeric: tabular-nums;
}
.ev-id {
    width: 4.75rem; flex: none; font-family: var(--font-mono, monospace);
    font-size: 11.5px; color: var(--color-fg-subtle);
}
.ev-id-link { color: var(--color-primary); text-decoration: none; }
.ev-id-link:hover { text-decoration: underline; }
.ev-title {
    flex: 1 1 auto; min-width: 0; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap; color: var(--color-fg);
}
.ev-ghost .ev-title { font-style: italic; white-space: normal; }
.ev-cost {
    width: 8.5rem; flex: none; text-align: right;
    font-family: var(--font-mono, monospace); font-size: 12px;
    font-variant-numeric: tabular-nums; color: var(--color-fg);
}
.ev-cost.is-gap { color: var(--color-amber-700); font-style: italic; }
.ev-cost.is-elsewhere { color: var(--color-fg-faint); font-style: italic; }
.ev-tag {
    flex: none; border-radius: 999px; background: var(--color-slate-100);
    color: var(--color-slate-600); padding: 0.05rem 0.4rem; font-size: 10px;
    white-space: nowrap;
}
.ev-tag-shared { background: var(--color-amber-50); color: var(--color-amber-700); }
.ev-tag-link { color: var(--color-primary); text-decoration: none; }
.ev-tag-link:hover { text-decoration: underline; }
.ev-undeclare { flex: none; opacity: 0.3; transition: opacity 150ms; }
.ev-row:hover .ev-undeclare, .ev-undeclare:focus-visible { opacity: 1; }
.ev-undeclare:hover {
    background: var(--color-danger-soft); color: var(--color-danger);
}
.ev-empty {
    border: 1px dashed var(--color-border-strong); border-radius: 0.6rem;
    padding: 0.9rem 1rem; color: var(--color-fg-muted);
}
.ev-empty strong {
    display: block; font-family: var(--font-mono, monospace);
    font-size: 13px; color: var(--color-fg-subtle);
}
.ev-empty p { margin-top: 0.3rem; font-size: 12px; max-width: 46ch; }
.ev-zero { text-decoration: line-through; color: var(--color-fg-faint); }
.ev-gaps {
    display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap;
    margin-top: 0.5rem; font-size: 11.5px; color: var(--color-amber-800);
}
.ev-gaps b { font-variant-numeric: tabular-nums; }
@media (max-width: 640px) {
    .ev-row, .ev-ghost { flex-wrap: wrap; }
    .ev-title { flex-basis: 100%; order: 9; }
    .ev-cost { width: auto; margin-left: auto; }
}
</style>
