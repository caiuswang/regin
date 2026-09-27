<script setup>
// The four figures, each carrying its own gap counter.
//
// Three devices answer the same worry — that a qualifier loses to the number
// it qualifies:
//   · the `≤` glyph is INSIDE the number, at the number's own size. A crop of
//     the numeral alone still reads as a bound, which an 11px grey caption or
//     a tooltip cannot promise.
//   · "upper bound" is an eyebrow chip in the tile header, not a sub-caption.
//   · a gap rides IN its own tile, never in one folded strip below the band —
//     a reader cannot tell which number `no-usage-captured` qualifies once it
//     has been pooled with three others.
//
// The amber escalation is conditional. With every tile alarmed at once the
// alarm stops meaning anything, so it fires only where a gap count exceeds
// the count it qualifies — the point at which the absence is the larger fact.
import { computed, nextTick } from 'vue'
import Icon from '../ui/Icon.vue'
import { duration, gapList, gapTotal, money, num, percent } from '../../utils/ledgerFormat'
import { DEFECTS_ANCHOR, SESSIONS_ANCHOR, defectsLink, reveal, revealSessions } from '../../utils/featureLinks'

const props = defineProps({
  ledger: { type: Object, required: true },
})

function tile(spec) {
  const gaps = gapList(spec.gaps)
  return { ...spec, gaps, escalate: gapTotal(spec.gaps) > spec.qualifies }
}

const tiles = computed(() => {
  const l = props.ledger
  const sessions = l.sessions.rows.length
  return [
    tile({
      key: 'cost', label: 'cost', qualifier: 'upper bound',
      glyph: l.cost.usd === null ? '' : '≤',
      value: l.cost.usd === null ? 'none declared' : money(l.cost.usd),
      empty: l.cost.usd === null,
      sub: l.cost.usd === null
        ? 'no session declared — not $0.00'
        : `${l.cost.sessions} attributed`
          + (l.cost.recalled ? ` · ${l.cost.recalled} recalled` : '')
          + (l.cost.shared ? ` · ${l.cost.shared} shared` : ''),
      gaps: l.cost.gaps, qualifies: l.cost.sessions,
    }),
    tile({
      key: 'work', label: 'work time', qualifier: 'upper bound',
      glyph: l.work_time.active_work_ms ? '≤' : '',
      value: duration(l.work_time.active_work_ms),
      empty: !l.work_time.active_work_ms,
      sub: 'active span time, whole sessions',
      gaps: l.work_time.gaps, qualifies: sessions,
    }),
    tile({
      key: 'sessions', label: 'sessions', qualifier: 'declared',
      glyph: '', value: sessions || 'none', empty: !sessions,
      sub: 'declared, never inferred',
      gaps: l.sessions.gaps, qualifies: sessions,
      // The evidence list below, which is where the traces are — the sessions
      // LIST takes no feature parameter, so a `/trace/sessions?feature=` would
      // be a second dead link rather than a repair of the first.
      //
      // The hash is what makes the position linkable; the handler is what
      // makes the click move the page, since a push to the route you are
      // already on is a navigation the router drops.
      to: sessions ? { hash: `#${SESSIONS_ANCHOR}` } : null,
      onClick: sessions ? () => nextTick(revealSessions) : null,
    }),
    tile({
      key: 'defects', label: 'defects', qualifier: 'local',
      glyph: '', value: `${l.defects.open} / ${l.defects.total}`,
      empty: !l.defects.total,
      sub: `open / total · ${l.defects.escapes} escaped`,
      gaps: l.defects.gaps, qualifies: l.defects.total,
      // A count with no way through to what it counts is how a defect stays
      // reported and unread. The list is this page's own, so the click has
      // to move the page, exactly as the sessions tile does.
      to: l.defects.total ? defectsLink(l.id) : null,
      onClick: l.defects.total ? () => nextTick(() => reveal(DEFECTS_ANCHOR)) : null,
    }),
  ]
})

const cov = computed(() => props.ledger.coverage)
// A floor, so a coverage of 0.4% is still a visible mark rather than an empty
// track a reader would read as "no data".
const covPct = computed(() =>
  Math.max(0.6, (100 * cov.value.declared_usd) / (cov.value.all_usd || 1)))

const tokenLine = computed(() => {
  const entries = Object.entries(props.ledger.tokens || {})
  if (!entries.some(([, v]) => v)) return ''
  return entries.map(([k, v]) => `${k.replace(/_/g, '-')} ${num(v)}`).join('  ·  ')
})
</script>

<template>
  <div class="space-y-3">
    <div class="grid gap-3 grid-cols-2 lg:grid-cols-4">
      <div
        v-for="t in tiles"
        :key="t.key"
        :class="['fig', t.escalate ? 'is-escalated' : '']"
        :data-testid="`figure-${t.key}`"
      >
        <div class="fig-head">
          <span class="fig-label">{{ t.label }}</span>
          <span class="fig-qual">{{ t.qualifier }}</span>
        </div>
        <div :class="['fig-value', t.empty ? 'is-empty' : '']">
          <span
            v-if="t.glyph"
            class="fig-glyph"
            aria-label="at most"
          >{{ t.glyph }}</span>{{ t.glyph ? ' ' : '' }}<router-link
            v-if="t.to" class="fig-link" :to="t.to" @click="t.onClick?.()"
          >{{ t.value }}</router-link><template v-else>{{ t.value }}</template>
        </div>
        <div class="fig-sub">{{ t.sub }}</div>
        <div v-if="t.gaps.length" class="fig-gaps">
          <Icon name="alert-triangle" :size="11" />
          <span v-for="g in t.gaps" :key="g.name" :title="g.blurb">
            <b>{{ g.count }}</b> {{ g.label }}
          </span>
        </div>
      </div>
    </div>

    <!-- Between the figures and the evidence, not under both: as a footer it
         fell below everything on an empty ledger and became the least-read
         thing on the page it permanently qualifies. -->
    <div class="cov">
      <div class="cov-head">
        <span class="cov-label">coverage of everything regin has recorded</span>
        <span class="cov-nums">
          {{ cov.declared_sessions }} / {{ num(cov.all_sessions) }} sessions
          ({{ percent(cov.declared_sessions, cov.all_sessions) }})
          ·
          {{ money(cov.declared_usd) }} / {{ money(cov.all_usd) }}
          ({{ percent(cov.declared_usd, cov.all_usd) }})
        </span>
      </div>
      <div class="cov-track">
        <div class="cov-fill" :style="{ width: covPct + '%' }"></div>
      </div>
    </div>

    <p v-if="tokenLine" class="tokens">{{ tokenLine }}</p>
  </div>
</template>

<style scoped>
.fig {
    position: relative; overflow: hidden;
    border: 1px solid var(--color-border); border-radius: var(--radius-xl);
    background: var(--color-surface); padding: 0.85rem 0.9rem;
}
.fig.is-escalated { border-color: var(--color-amber-200); }
.fig.is-escalated::before {
    content: ''; position: absolute; inset-block: 0; left: 0; width: 3px;
    background: var(--color-amber-400);
}
.fig-head { display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; }
.fig-label {
    font-size: 11px; font-weight: 600; letter-spacing: 0.06em;
    text-transform: uppercase; color: var(--color-fg-faint);
}
.fig-qual {
    border-radius: 999px; background: var(--color-slate-100);
    color: var(--color-slate-600); padding: 0.05rem 0.4rem; font-size: 10px;
}
.fig-value {
    margin-top: 0.45rem; font-size: 1.75rem; font-weight: 700; line-height: 1;
    color: var(--color-fg); font-variant-numeric: tabular-nums;
}
/* Absent figures SHRINK rather than shouting a placeholder: italic faint at
   1rem, never 28px bold, so an empty ledger does not read as a loud zero. */
.fig-value.is-empty {
    font-size: 1rem; font-weight: 500; font-style: italic;
    color: var(--color-fg-faint);
}
.fig-glyph { color: var(--color-fg-subtle); }
.fig-link { color: inherit; text-decoration: none; }
.fig-link:hover { text-decoration: underline; }
.fig-sub { margin-top: 0.4rem; font-size: 11.5px; color: var(--color-fg-subtle); }
.fig-gaps {
    display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;
    margin: 0.55rem -0.9rem -0.85rem; padding: 0.35rem 0.9rem;
    background: var(--color-amber-50); color: var(--color-amber-800);
    font-size: 11px;
}
.fig-gaps b { font-variant-numeric: tabular-nums; font-weight: 700; }
.cov {
    border: 1px solid var(--color-border); border-radius: var(--radius-xl);
    background: var(--color-slate-50); padding: 0.55rem 0.9rem;
}
.cov-head {
    display: flex; align-items: baseline; justify-content: space-between;
    gap: 1rem; flex-wrap: wrap;
}
.cov-label {
    font-size: 11px; letter-spacing: 0.05em; text-transform: uppercase;
    color: var(--color-fg-faint);
}
.cov-nums {
    font-size: 11.5px; color: var(--color-fg-muted);
    font-variant-numeric: tabular-nums;
}
.cov-track {
    margin-top: 0.4rem; height: 4px; border-radius: 999px;
    background: var(--color-slate-200); overflow: hidden;
}
.cov-fill { height: 100%; background: var(--color-slate-400); }
.tokens {
    font-family: var(--font-mono, monospace); font-size: 11px;
    color: var(--color-fg-faint);
}
</style>
