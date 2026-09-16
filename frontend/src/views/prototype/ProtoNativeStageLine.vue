<script setup>
// PROTOTYPE — throwaway (CAI-269). Do not ship.
//
// FeatureStageLine.vue with two things added: a `live` input (the candidate
// `stage_live` field) and a `variant` that decides what a live row renders
// where the age stamp and the Refresh button sit today. A row that is not
// live renders exactly what ships now, in every variant — that is the
// control.
import { computed } from 'vue'
import Badge from '../../components/Badge.vue'
import Button from '../../components/ui/Button.vue'
import Icon from '../../components/ui/Icon.vue'
import { STAGE_COLOR, asOf } from '../../utils/ledgerFormat'

const props = defineProps({
  featureKey: { type: String, required: true },
  stage: { type: Object, default: null },
  syncedAt: { type: String, default: null },
  live: { type: Boolean, default: false },
  variant: { type: String, default: 'A' },
})

const color = computed(() => STAGE_COLOR[props.stage?.stage] || 'gray')
const synced = computed(() => (props.stage ? asOf(props.syncedAt) : null))

const showStamp = computed(
  () => !props.live || props.variant === 'A' || props.variant === 'D')
const showLiveWord = computed(() => props.live && props.variant === 'B')
const showRefresh = computed(() => !props.live || props.variant === 'A')
</script>

<template>
  <section class="stage-line">
    <div class="stage-line-main">
      <span class="stage-key">{{ featureKey }}</span>

      <Badge v-if="stage" :color="color" :label="stage.stage" />
      <span v-else class="stage-none">
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

      <span v-if="synced && showStamp" class="stage-freshness">
        as of {{ synced }}
      </span>
      <span v-if="showLiveWord" class="stage-live">live</span>

      <Button
        v-if="showRefresh"
        class="stage-refresh"
        variant="secondary" size="sm"
        :aria-label="`Refresh the stage for ${featureKey} from the tracker`"
      >
        <Icon name="refresh-cw" :size="12" />
        Refresh stage
      </Button>
    </div>
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
    font-size: 11px; color: var(--color-fg-subtle);
    font-variant-numeric: tabular-nums;
}
.stage-live {
    display: inline-flex; align-items: center; gap: 0.3rem;
    font-size: 11px; color: var(--color-fg-subtle);
}
.stage-live::before {
    content: ""; width: 5px; height: 5px; border-radius: 999px;
    background: var(--color-green-500, #22c55e);
}
.stage-refresh { flex: none; margin-left: auto; }
</style>
