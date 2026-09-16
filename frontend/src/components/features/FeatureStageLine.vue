<script setup>
// The stage, its freshness, and the one control on this page that reaches the
// tracker.
//
// It leads the page — the ordering `regin feature ledger` asserts, because
// `$31.80` means something different on a `spec` than on a `shipped` — but as
// a LINE, not a band. A full-width stage card was the largest object on the
// empty ledger most features will be for a long time, which inverts the
// framing job: the stage frames by being read first, not by being bigger.
//
// Three rules hold at every placement:
//   · `stage: null` renders NO chip. A missing stage that wears one reads as a
//     stage somebody declared.
//   · freshness rides BESIDE the chip, never inside it — `Stage.to_dict()` is
//     flat, and `null` can carry no field.
//   · a failed refresh leaves the stage and its timestamp untouched and says
//     so next to them. Nothing was persisted, so it must not look like state.
//
// A LIVE tracker takes the freshness slot rather than sharing it. Its issues
// live in regin's own database, so the cache cannot lag the fact: there is
// nothing to be as-of and nothing to refresh, and rendering `as of T` beside
// a figure that is always current would be a qualifier with no referent. The
// Refresh control goes with it — `canRefresh` is `canWrite && !live`, which
// hides the control without the endpoint behind it changing at all.
import { computed } from 'vue'
import Badge from '../Badge.vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import { STAGE_COLOR, asOf } from '../../utils/ledgerFormat'

const props = defineProps({
  featureKey: { type: String, required: true },
  stage: { type: Object, default: null },
  syncedAt: { type: String, default: null },
  refreshing: { type: Boolean, default: false },
  failed: { type: String, default: '' },
  canRefresh: { type: Boolean, default: true },
  // Declared by the provider, resolved server-side through a registry lookup.
  // Never inferred from the key's shape here: the shape is a provider's own
  // business, and reading it would be the vendor branch the seam forbids.
  live: { type: Boolean, default: false },
})
defineEmits(['refresh'])

const color = computed(() => STAGE_COLOR[props.stage?.stage] || 'gray')
// A timestamp only means anything next to a stage: `stage_synced_at` is
// written by a SUCCESSFUL fetch, so a null stage implies a null timestamp.
// The two are beside each other but not orthogonal.
const synced = computed(() => (props.stage ? asOf(props.syncedAt) : null))
</script>

<template>
  <section class="stage-line">
    <div class="stage-line-main">
      <span class="stage-key">{{ featureKey }}</span>

      <Badge v-if="stage" :color="color" :label="stage.stage" />

      <!-- No tracker answered. Deliberately not a chip: plain text, in the
           voice of the reader's own tooling, because it is a statement about
           us and not about the feature. -->
      <span v-else class="stage-none" data-testid="stage-not-fetched">
        <Icon name="alert-triangle" :size="12" />
        stage not fetched
      </span>

      <span v-if="stage" class="stage-blurb">{{ stage.blurb }}</span>

      <span v-if="stage" class="stage-counts">
        <span><b>{{ stage.rounds }}</b> rounds</span>
        <span><b>{{ stage.open_defects }}</b> open</span>
        <span :class="stage.escapes ? 'text-amber-700' : ''">
          <b>{{ stage.escapes }}</b> escaped
        </span>
      </span>

      <!-- A live tracker states that it is live, and says nothing about when
           it was last read — the question does not arise. -->
      <span v-if="live && stage" class="stage-live" data-testid="stage-live">
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
        :aria-label="`Refresh the stage for ${featureKey} from the tracker`"
        @click="$emit('refresh')"
      >
        <Icon name="refresh-cw" :size="12" />
        {{ refreshing ? 'Refreshing' : 'Refresh stage' }}
      </Button>
    </div>

    <!-- Inline, never a toast: the reader has to be able to see the claim and
         the stage it qualifies at the same time. -->
    <p v-if="failed" class="stage-fail" role="status">
      <Icon name="alert-triangle" :size="12" />
      <span>
        The tracker could not answer — {{ failed }}.
        <template v-if="stage">
          The stage beside it is unchanged, still as of {{ synced }}.
        </template>
        <template v-else>
          Nothing has ever been fetched for {{ featureKey }}.
        </template>
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
.stage-none {
    display: inline-flex; align-items: center; gap: 0.25rem;
    font-size: 12px; font-style: italic; color: var(--color-fg-subtle);
    border-bottom: 1px dashed var(--color-border-strong); padding-bottom: 1px;
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
.stage-refresh { flex: none; margin-left: auto; }
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
