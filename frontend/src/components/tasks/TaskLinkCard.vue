<script setup>
// One linked issue, in whichever tracker holds it.
//
// Everything rendered here comes off the canonical snapshot shape
// (lib/tasks/integrations/base.py), never a vendor field — that is what lets
// the second platform land without touching this file. `extra` is the escape
// hatch for the one-off facts only one tracker has, rendered as opaque rows.
import { computed } from 'vue'
import Badge from '../Badge.vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import { STAGE_COLOR, asOf } from '../../utils/ledgerFormat'

const props = defineProps({
  link: { type: Object, required: true },
  busy: { type: Boolean, default: false },
})
defineEmits(['refresh', 'detach'])

const STATE_COLOR = {
  triage: 'yellow', backlog: 'gray', unstarted: 'blue',
  started: 'purple', completed: 'green', canceled: 'red',
}

// Where the feature stands in the pipeline (docs/feature-pipeline.md),
// computed server-side from declared facts. `escaped` is amber on purpose: a
// feature that shipped and has had bugs blamed on it since is the one signal
// this whole surface exists to make unmissable. It replaced `reworking`, which
// keyed on a reopened issue — something the pipeline now forbids, so the amber
// could never fire again.
//
// The map is shared with the feature ledger rather than copied: three surfaces
// rendering one stage from two maps is how they start disagreeing about what
// `escaped` looks like.

const issue = computed(() => props.link.issue || null)
const stateColor = computed(
  () => STATE_COLOR[issue.value?.state_category] || 'gray')
const extras = computed(() => Object.entries(issue.value?.extra || {}))
// Absent until the link has synced at least once — `spec` is a real stage and
// must not stand in for "nobody has looked".
const stage = computed(() => props.link.stage || null)
const stageColor = computed(() => STAGE_COLOR[stage.value?.stage] || 'gray')
// Whether this tracker's cached stage can lag the fact. Declared by the
// provider and resolved server-side; a live one states that it is live rather
// than dating a read that is always current, and drops its Refresh with it.
const live = computed(() => props.link.stage_live === true)
// Keyed on the URL's SHAPE, not on the provider: a relative url is an in-app
// route and an absolute one leaves the app, which is true of any tracker that
// ever lives here. Reading `provider` instead would put a vendor branch in the
// renderer the seam exists to keep vendor-free.
const isInternal = computed(() => !/^https?:\/\//i.test(props.link.url || ''))
</script>

<template>
  <li class="tl-card" :data-testid="`task-link-${link.id}`">
    <div class="tl-head">
      <Badge color="cyan" :label="link.provider" />
      <router-link
        v-if="isInternal"
        class="tl-key rounded-sm focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-1"
        :to="link.url">
        {{ link.external_key }}
      </router-link>
      <a
        v-else
        class="tl-key rounded-sm focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-1"
        :href="link.url" target="_blank" rel="noopener noreferrer">
        {{ link.external_key }}
        <Icon name="external-link" :size="11" />
      </a>
      <Badge v-if="issue?.state" :color="stateColor" :label="issue.state" />
      <Badge
        v-if="stage"
        :color="stageColor"
        :label="stage.stage"
        :title="stage.blurb"
        :data-testid="`task-link-stage-${link.id}`"
      />
      <!-- The card's two subjects, one link each: the key above is what the
           tracker says, this is what regin says. Present for every link row
           regardless of stage — the stage is absent exactly when the ledger is
           most worth opening, so gating the link on it would hide the door on
           the keys that need it. It is deliberately NOT the stage chip: a chip
           is a figure, and a rule that varies by placement is the one the
           shared component exists to avoid. -->
      <router-link
        class="tl-ledger rounded-sm focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-1"
        :to="`/features/${link.external_key}`"
        :data-testid="`task-link-ledger-${link.id}`"
      >ledger →</router-link>
      <div class="tl-actions">
        <Button
          v-if="!live"
          variant="ghost" :disabled="busy"
          :aria-label="`Refresh ${link.external_key}`"
          :data-testid="`task-link-refresh-${link.id}`"
          @click="$emit('refresh', link.id)"
        >
          <Icon name="refresh-cw" :size="12" />
        </Button>
        <Button
          variant="ghost" :disabled="busy"
          :aria-label="`Unlink ${link.external_key}`"
          :data-testid="`task-link-detach-${link.id}`"
          @click="$emit('detach', link.id)"
        >
          <Icon name="trash" :size="12" />
        </Button>
      </div>
    </div>

    <p v-if="issue?.title" class="tl-title">{{ issue.title }}</p>

    <div class="tl-facts">
      <span
        v-if="stage"
        :data-testid="`task-link-rounds-${link.id}`"
      >{{ stage.rounds }} round{{ stage.rounds === 1 ? '' : 's' }}</span>
      <span
        v-if="stage?.escapes"
        class="tl-rework"
        :data-testid="`task-link-escapes-${link.id}`"
      >{{ stage.escapes }} escape{{ stage.escapes === 1 ? '' : 's' }}</span>
      <span
        v-if="stage?.open_defects"
        class="tl-rework"
        :data-testid="`task-link-open-defects-${link.id}`"
      >{{ stage.open_defects }} open defect{{ stage.open_defects === 1 ? '' : 's' }}</span>
      <span v-if="issue?.assignee">{{ issue.assignee }}</span>
      <span v-if="issue?.priority">priority {{ issue.priority }}</span>
      <span v-for="pair in extras" :key="pair[0]">{{ pair[0] }}: {{ pair[1] }}</span>
      <span v-if="live" :data-testid="`task-link-live-${link.id}`">live</span>
      <span v-else-if="link.synced_at">synced {{ asOf(link.synced_at) }}</span>
      <span v-else class="tl-never" :data-testid="`task-link-never-${link.id}`">
        stage not fetched
      </span>
    </div>

    <div v-if="issue?.labels?.length" class="tl-labels">
      <Badge v-for="label in issue.labels" :key="label" color="purple" :label="label" />
    </div>

    <p v-if="issue?.description" class="tl-desc">{{ issue.description }}</p>

    <p
      v-if="link.sync_error"
      class="tl-error"
      role="status"
      :data-testid="`task-link-error-${link.id}`"
    >{{ link.sync_error }}</p>
  </li>
</template>

<style scoped>
.tl-card {
    padding: 0.625rem 0.75rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    background: var(--color-surface-2);
    min-width: 0;
}
.tl-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.375rem;
}
.tl-ledger {
    flex: none; font-size: 11px; font-weight: 600;
    color: var(--color-primary); text-decoration: none;
}
.tl-ledger:hover { text-decoration: underline; }
.tl-actions {
    display: flex;
    align-items: center;
    gap: 0.125rem;
    margin-left: auto;
}
.tl-key {
    display: inline-flex;
    align-items: center;
    gap: 0.1875rem;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.8125rem;
    font-weight: 600;
    color: var(--color-fg);
    text-decoration: none;
}
.tl-key:hover { text-decoration: underline; }
.tl-title {
    margin: 0.25rem 0 0;
    font-size: 0.8125rem;
    color: var(--color-fg);
    overflow-wrap: anywhere;
}
.tl-facts {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 0.25rem;
    font-size: 0.75rem;
    color: var(--color-fg-faint);
}
.tl-never { font-style: italic; }
.tl-rework {
    color: var(--color-warning-fg, var(--color-fg));
    font-weight: 600;
}
.tl-labels {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem;
    margin-top: 0.375rem;
}
.tl-desc {
    margin: 0.5rem 0 0;
    font-size: 0.75rem;
    color: var(--color-fg-muted);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 12rem;
    overflow-y: auto;
}
.tl-error {
    margin: 0.5rem 0 0;
    font-size: 0.75rem;
    color: var(--color-danger-fg, var(--color-fg-muted));
    overflow-wrap: anywhere;
}
</style>
