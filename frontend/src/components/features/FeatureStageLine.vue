<script setup>
// The stage, the done-claim, and the one control on this page that reaches a
// tracker.
//
// It leads the page — the ordering `regin feature ledger` asserts, because
// `$31.80` means something different on a `spec` than on a `shipped` — but as
// a LINE, not a band. A full-width stage card was the largest object on the
// empty ledger most features will be for a long time, which inverts the
// framing job: the stage frames by being read first, not by being bigger.
//
// **There is no "stage not fetched" any more.** Every feature carries its own
// `specced` event, so the stage is always a real answer and the chip is always
// a stage somebody's act produced. The branch that rendered a missing one is
// gone with the fetch that could fail to fill it.
//
// Two rules hold at every placement:
//   · freshness rides BESIDE the chip, never inside it — it is a fact about a
//     fetch, not about where the feature stands.
//   · a failed refresh leaves the stage and its timestamp untouched and says
//     so next to them. Nothing was persisted, so it must not look like state.
//
// A NATIVE feature takes the freshness slot rather than sharing it. Its stage
// is derived from rows in this database, so it cannot lag the fact: there is
// nothing to be as-of and nothing to refresh, and rendering `as of T` beside a
// figure that is always current would be a qualifier with no referent. The
// Refresh control goes with it — `canRefresh` is `canWrite && !live`, which
// hides the control without the endpoint behind it changing at all.
import { computed } from 'vue'
import Badge from '../Badge.vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import { STAGE_COLOR, asOf } from '../../utils/ledgerFormat'

const props = defineProps({
  featureId: { type: Number, required: true },
  title: { type: String, default: '' },
  // The tracker reference, when this feature has one. Rendered beside the
  // title rather than in place of it: `features.title` is the authority for
  // what is being delivered, and the reference says where it is tracked.
  externalRef: { type: String, default: null },
  externalUrl: { type: String, default: null },
  stage: { type: Object, required: true },
  syncedAt: { type: String, default: null },
  refreshing: { type: Boolean, default: false },
  failed: { type: String, default: '' },
  canRefresh: { type: Boolean, default: true },
  canShip: { type: Boolean, default: false },
  // Taking a feature down. Distinct from `canShip` because the two are not
  // opposites: an abandoned feature can be neither shipped nor abandoned
  // again, while a shipped one can still be taken down.
  canAbandon: { type: Boolean, default: false },
  // Which write is in flight, so the whole row disables together: a second
  // click during a ship appends a second event to an append-only log.
  busy: { type: String, default: '' },
  // Resolved server-side from the feature's origin. Never inferred from a
  // reference's shape here: the shape is a provider's own business, and
  // reading it would be the vendor branch the seam forbids.
  live: { type: Boolean, default: false },
})
defineEmits(['refresh', 'ship', 'abandon'])

const color = computed(() => STAGE_COLOR[props.stage?.stage] || 'gray')
// Only a successful fetch writes it, so a feature nobody has synced has no
// "as of T" — and a native one never will.
const synced = computed(() => asOf(props.syncedAt))
</script>

<template>
  <section class="stage-line">
    <div class="stage-line-main">
      <span class="stage-key">{{ title }}</span>

      <!-- The reference, where there is one. A link, because an operator who
           reads it wants to open it. -->
      <a
        v-if="externalRef"
        class="stage-ref"
        :href="externalUrl || undefined"
        target="_blank"
        rel="noopener"
        data-testid="stage-ref"
      >{{ externalRef }}</a>

      <Badge :color="color" :label="stage.stage" />

      <span class="stage-blurb">{{ stage.blurb }}</span>

      <span class="stage-counts">
        <span><b>{{ stage.rounds }}</b> rounds</span>
        <span><b>{{ stage.open_defects }}</b> open</span>
        <span :class="stage.escapes ? 'text-amber-700' : ''">
          <b>{{ stage.escapes }}</b> escaped
        </span>
      </span>

      <!-- A feature whose stage is derived here states that it is live, and
           says nothing about when it was last read — the question does not
           arise. -->
      <span v-if="live" class="stage-live" data-testid="stage-live">
        <span class="stage-dot" aria-hidden="true"></span>live
      </span>

      <!-- The last successful fetch survives an in-flight refresh: blanking it
           makes the page briefly claim to know nothing about its own
           freshness. -->
      <span v-else-if="synced || refreshing" class="stage-freshness">
        <span v-if="refreshing" class="stage-spin" aria-hidden="true"></span>
        <template v-if="synced">as of {{ synced }}</template>
        <template v-if="refreshing">
          <template v-if="synced"> · </template>asking the tracker…
        </template>
      </span>

      <Button
        v-if="canRefresh"
        class="stage-refresh"
        variant="secondary" size="sm"
        :disabled="refreshing"
        :aria-label="`Refresh ${externalRef} from the tracker`"
        @click="$emit('refresh')"
      >
        <Icon name="refresh-cw" :size="12" />
        {{ refreshing ? 'Refreshing' : 'Refresh stage' }}
      </Button>

      <!-- The done-claim, and the only control that writes it. A run settling
           says *this session finished*, never *this shipped*. -->
      <Button
        v-if="canShip"
        class="stage-ship"
        variant="secondary" size="sm"
        data-testid="ship-feature"
        :disabled="Boolean(busy)"
        :aria-label="`Claim ${title} shipped`"
        @click="$emit('ship')"
      >
        <Icon name="check" :size="12" />
        {{ busy === 'ship' ? 'Shipping…' : 'Ship' }}
      </Button>

      <!-- Taking it down. `abandoned` is a stage, not a delete: the events are
           the only source of this feature's rounds and its escapes, so removing
           one would not be a cleanup but a rewrite of history. The confirm is
           the caller's — one confirm path for every destructive act (R5). -->
      <Button
        v-if="canAbandon"
        class="stage-abandon"
        variant="danger" size="sm"
        data-testid="abandon-feature"
        :disabled="Boolean(busy)"
        :aria-label="`Abandon ${title}`"
        @click="$emit('abandon')"
      >
        <Icon name="trash" :size="12" />
        {{ busy === 'abandon' ? 'Abandoning…' : 'Abandon' }}
      </Button>
    </div>

    <!-- Inline, never a toast: the reader has to be able to see the claim and
         the stage it qualifies at the same time. -->
    <p v-if="failed" class="stage-fail" role="status">
      <Icon name="alert-triangle" :size="12" />
      <span>
        The tracker could not answer — {{ failed }}.
        The stage beside it is unchanged<template v-if="synced">, still as of
        {{ synced }}</template>.
        Every other figure on this page is local and unaffected.
      </span>
    </p>
  </section>
</template>

<style scoped>
.stage-line { display: flex; flex-direction: column; gap: 0.5rem; }
.stage-line-main {
    display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap;
}
.stage-key {
    font-family: var(--font-mono, monospace); font-weight: 700;
    font-size: 0.95rem; color: var(--color-fg);
}
.stage-blurb { font-size: 12px; color: var(--color-fg-muted); }
.stage-counts {
    display: flex; gap: 0.85rem; font-size: 11.5px;
    color: var(--color-fg-subtle);
}
.stage-counts b {
    color: var(--color-fg); font-variant-numeric: tabular-nums;
}
.stage-freshness {
    display: inline-flex; align-items: center; gap: 0.35rem;
    font-size: 11px; color: var(--color-fg-subtle);
    font-variant-numeric: tabular-nums;
}
.stage-live {
    display: inline-flex; align-items: center; gap: 0.3rem;
    font-size: 11px; color: var(--color-fg-subtle);
}
.stage-dot {
    width: 6px; height: 6px; border-radius: 999px;
    background: var(--color-green-500, #22c55e);
}
.stage-ref {
    font-family: var(--font-mono, monospace); font-size: 11.5px;
    color: var(--color-fg-subtle); text-decoration: none;
    border-bottom: 1px dotted var(--color-border-strong);
}
.stage-ref:hover { color: var(--color-fg); }
.stage-refresh { flex: none; margin-left: auto; }
.stage-ship { flex: none; }
.stage-abandon { flex: none; }
.stage-refresh + .stage-ship { margin-left: 0; }
.stage-line-main > .stage-ship:first-of-type { margin-left: auto; }
.stage-spin {
    width: 10px; height: 10px; border-radius: 999px;
    border: 2px solid var(--color-slate-300);
    border-top-color: var(--color-blue-500);
    animation: stage-spin 700ms linear infinite;
}
@keyframes stage-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
    .stage-spin { animation: none; }
}
.stage-fail {
    display: flex; align-items: flex-start; gap: 0.4rem;
    border-radius: 0.5rem; background: var(--color-amber-50);
    color: var(--color-amber-800); padding: 0.4rem 0.6rem; font-size: 11.5px;
}
</style>
