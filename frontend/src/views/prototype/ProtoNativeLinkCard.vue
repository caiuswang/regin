<script setup>
// PROTOTYPE — throwaway (CAI-269). Do not ship.
//
// TaskLinkCard.vue cut down to the parts CAI-269 touches: the provider badge,
// the key's affordance, the facts row that ends in `synced …`, and the
// Refresh button. Same `live`/`variant` rule as the stage line, so the two
// surfaces can be judged against each other rather than one at a time.
import { computed } from 'vue'
import Badge from '../../components/Badge.vue'
import Button from '../../components/ui/Button.vue'
import Icon from '../../components/ui/Icon.vue'
import { STAGE_COLOR, asOf } from '../../utils/ledgerFormat'

const props = defineProps({
  link: { type: Object, required: true },
  variant: { type: String, default: 'A' },
})

const live = computed(() => Boolean(props.link.stage_live))
const stageColor = computed(
  () => STAGE_COLOR[props.link.stage?.stage] || 'gray')
const showStamp = computed(
  () => !live.value || props.variant === 'A' || props.variant === 'D')
const showLiveWord = computed(() => live.value && props.variant === 'B')
const showRefresh = computed(() => !live.value || props.variant === 'A')
// A native url is a route in this very SPA. `target="_blank"` plus an
// external-link glyph on it is the same small lie as the age stamp.
const external = computed(() => !live.value)
</script>

<template>
  <li class="tl-card">
    <div class="tl-head">
      <Badge color="cyan" :label="link.provider" />
      <a
        v-if="external"
        class="tl-key rounded-sm focus-visible:ring-2 focus-visible:ring-blue-500"
        :href="link.url"
        target="_blank" rel="noopener noreferrer"
      >{{ link.external_key }} <Icon name="external-link" :size="11" /></a>
      <router-link
        v-else
        class="tl-key rounded-sm focus-visible:ring-2 focus-visible:ring-blue-500"
        :to="link.url"
      >
        {{ link.external_key }}
      </router-link>
      <Badge color="gray" :label="link.issue.state" />
      <Badge
        v-if="link.stage" :color="stageColor" :label="link.stage.stage"
        :title="link.stage.blurb"
      />
      <router-link
        class="tl-ledger rounded-sm focus-visible:ring-2 focus-visible:ring-blue-500"
        :to="`/features/${link.external_key}`"
      >
        ledger →
      </router-link>
      <div class="tl-actions">
        <Button
          v-if="showRefresh" variant="ghost"
          :aria-label="`Refresh ${link.external_key}`"
        ><Icon name="refresh-cw" :size="12" /></Button>
        <Button variant="ghost" :aria-label="`Unlink ${link.external_key}`">
          <Icon name="trash" :size="12" />
        </Button>
      </div>
    </div>

    <p class="tl-title">{{ link.issue.title }}</p>

    <div class="tl-facts">
      <span>{{ link.stage.rounds }} rounds</span>
      <span v-if="link.stage.open_defects">
        {{ link.stage.open_defects }} open defect
      </span>
      <span>{{ link.issue.assignee }}</span>
      <span>project: {{ link.issue.extra.project }}</span>
      <span v-if="showStamp">synced {{ asOf(link.synced_at) }}</span>
      <span v-if="showLiveWord" class="tl-live">live</span>
    </div>
  </li>
</template>

<style scoped>
.tl-card {
    padding: 0.625rem 0.75rem; border: 1px solid var(--color-border);
    border-radius: var(--radius-lg); background: var(--color-surface-2);
    min-width: 0; list-style: none;
}
.tl-head {
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.375rem;
}
.tl-key {
    display: inline-flex; align-items: center; gap: 0.1875rem;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.8125rem; font-weight: 600; color: var(--color-fg);
    text-decoration: none;
}
.tl-key:hover { text-decoration: underline; }
.tl-ledger {
    flex: none; font-size: 11px; font-weight: 600;
    color: var(--color-primary); text-decoration: none;
}
.tl-actions {
    display: flex; align-items: center; gap: 0.125rem; margin-left: auto;
}
.tl-title {
    margin: 0.25rem 0 0; font-size: 0.8125rem; color: var(--color-fg);
}
.tl-facts {
    display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.25rem;
    font-size: 0.75rem; color: var(--color-fg-faint);
}
.tl-live { display: inline-flex; align-items: center; gap: 0.3rem; }
.tl-live::before {
    content: ""; width: 5px; height: 5px; border-radius: 999px;
    background: var(--color-green-500, #22c55e);
}
</style>
