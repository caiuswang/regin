<script setup>
// One defect: a one-line head that is the same on every list, and behind it
// the full report — the symptom unclamped (pictures in place), the facts, and
// the row's verbs.
//
// The head is a fixed-track grid, so every row of a list puts its id, status
// and fix task at the same x. The symptom's first line is all the head shows
// of the wording; the reporter's full text is one click away, never clamped.
// Open/closed lives only here: it is a reading position, not a fact about the
// defect, so nothing persists it.
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import DefectActions from './DefectActions.vue'
import DefectFixCell from './DefectFixCell.vue'
import DefectImages from './DefectImages.vue'
import DefectSymptom from './DefectSymptom.vue'
import PlGlyph from '../pipeline/PlGlyph.vue'
import { useAttachedImages } from '../../composables/useAttachedImages.js'
import { useRefResolve } from '../../composables/useRefResolve.js'
import { shortDate } from '../../utils/ledgerFormat'
import { utcTitle } from '../../utils/time.js'
import { reopenedLabel, symptomFirstLine } from '../../utils/pipelineFormat'

const props = defineProps({
  defect: { type: Object, required: true },
  canWrite: { type: Boolean, default: false },
  // Set where the list is already scoped to one feature: every row would
  // otherwise repeat the title the page is headed by.
  hideFeature: { type: Boolean, default: false },
  // A row in a list of rows (the feature page's ledger grammar) rather than
  // a free-standing card.
  flat: { type: Boolean, default: false },
  // A list row: only the head at rest, the report behind a click.
  collapsible: { type: Boolean, default: false },
  // The list's batch-resolved `{ 'task:N': RefItem }`. Null on a lone row,
  // which resolves its own one fix task instead.
  fixItems: { type: Object, default: null },
})
const emit = defineEmits(['changed'])

const route = useRoute()
const open = ref(!props.collapsible)
const head = ref(null)
const anchor = computed(() => `defect-${props.defect.id}`)
watch(() => route?.hash, (hash) => {
  if (props.collapsible && hash === `#${anchor.value}`) open.value = true
}, { immediate: true })

// One list for the evidence strip and the pictures the symptom cites, bound
// only once the row is open: a collapsed row draws neither.
const images = useAttachedImages('defects')
watch([open, () => props.defect.id, () => props.defect.images?.length],
      () => { if (open.value) images.bind(props.defect) }, { immediate: true })

const fixRef = computed(() => (props.defect.fix_task_id ? `task:${props.defect.fix_task_id}` : ''))
const own = useRefResolve(() => (props.fixItems || !fixRef.value ? [] : [fixRef.value]))
const fixItem = computed(() => (props.fixItems || own.items.value)[fixRef.value])

const heldOut = computed(() => ['snoozed', 'declined'].includes(props.defect.queue_position))
// An open report nobody has blamed is the list's call to action (R7: framed, not filled).
const unblamed = computed(() => props.defect.status === 'open' && !props.defect.feature_id)
const firstLine = computed(() => symptomFirstLine(props.defect.symptom))

// The head toggles, but not when the click was meant for something in it.
const INTERACTIVE = 'a, button, input, label, textarea, select, [role="checkbox"], [role="button"]'
function onHeadClick(e) {
  if (!props.collapsible || e.target?.closest?.(INTERACTIVE)) return
  open.value = !open.value
}
function toggle() { open.value = !open.value }
async function collapse() {
  if (!props.collapsible || !open.value) return
  open.value = false
  await nextTick()
  head.value?.focus()
}
</script>

<template>
  <li
    :id="anchor"
    :class="[flat ? 'ledger-row defect-flat' : 'card', { 'is-collapsed': !open }]"
    :data-testid="`defect-row-${defect.id}`"
    :data-pl-part="flat ? 'ledger-row' : 'card'"
    :data-status="defect.status"
    :data-pl-unblamed="unblamed ? '' : undefined"
    :data-expanded="collapsible && open ? '' : undefined"
    @keydown.esc="collapse"
  >
    <div
      ref="head"
      class="head cursor-pointer rounded-md hover:bg-[var(--color-surface-2)] focus-visible:outline-2 focus-visible:outline-blue-500"
      :class="{ 'has-feature': !hideFeature }"
      :data-pl-part="flat ? 'ledger-head' : 'card-head'"
      :data-testid="`defect-head-${defect.id}`"
      :tabindex="collapsible ? 0 : undefined"
      @click="onHeadClick"
      @keydown.enter.self.prevent="collapsible && toggle()"
      @keydown.space.self.prevent="collapsible && toggle()"
    >
      <span class="cell" data-cell="select"><slot name="select" /></span>
      <!-- The id is the row's address. A list is a predicate plus a page
           position, and neither survives being sent to someone else; a
           defect that cannot be linked to can only be pointed at by
           describing it. -->
      <span class="cell cell-id" data-cell="id">
        <PlGlyph :kind="defect.status === 'open' ? 'defect.wild' : 'defect.closed'" />
        <router-link class="ledger-id id" :to="`/defects/${defect.id}`"
                     :data-testid="`defect-permalink-${defect.id}`"
                     data-pl-part="row-id"
        >#{{ defect.id }}</router-link>
      </span>
      <span class="cell cell-symptom" data-cell="symptom">
        <slot name="flags" />
        <span v-if="reopenedLabel(defect.round)" class="ledger-tag" data-pl-part="tag" data-pl-dashed="">{{ reopenedLabel(defect.round) }}</span>
        <!-- Held out of the queue, and only while it is. Separate from `status`
             because a declined report and a fixed bug are both closed, and only
             one of them was ever looked at. `queue_position`, not
             `triage_state`: a snoozed report that woke still carries the old
             state but is back in the queue, offering the queue's verbs. -->
        <span v-if="heldOut" class="ledger-tag" data-pl-part="tag" data-pl-dashed=""
              :data-testid="`defect-triage-${defect.id}`">{{ defect.queue_position }}</span>
        <span class="line" data-pl-part="symptom-line" :title="defect.symptom"
              :data-testid="`defect-line-${defect.id}`">{{ firstLine }}</span>
      </span>
      <span v-if="!hideFeature" class="cell" data-cell="feature">
        <router-link v-if="defect.feature_id" class="key"
                     :to="`/features/${defect.feature_id}`" data-pl-part="feature-link"
                     :title="defect.feature_title || undefined">
          {{ defect.feature_title || `#${defect.feature_id}` }}
        </router-link>
        <span v-else class="unblamed" data-pl-part="state" data-tone="warning">unattributed</span>
      </span>
      <span class="cell" data-cell="status">
        <span class="ledger-tag"
              :class="defect.status === 'open' ? 'ledger-tag-amber' : 'ledger-tag-green'"
              data-pl-part="tag" :data-tone="defect.status === 'open' ? 'warn' : 'ship'"
        >{{ defect.status }}</span>
      </span>
      <span class="cell" data-cell="fix">
        <DefectFixCell :defect="defect" :item="fixItem" />
      </span>
      <span class="cell when" data-cell="date" :title="utcTitle(defect.opened_at)"
            data-pl-part="meta-label">{{ shortDate(defect.opened_at) }}</span>
      <span class="cell" data-cell="toggle">
        <Button
          v-if="collapsible"
          variant="ghost"
          size="icon"
          class="toggle"
          :aria-expanded="open"
          :aria-label="`${open ? 'Hide' : 'Show'} details and actions for defect #${defect.id}`"
          :data-testid="`defect-toggle-${defect.id}`"
          data-pl-part="key"
          @click="toggle"
        >
          <Icon :name="open ? 'chevron-down' : 'chevron-right'" :size="13" />
        </Button>
      </span>
    </div>

    <div v-if="open" class="body" :data-testid="`defect-body-${defect.id}`">
      <DefectSymptom :defect="defect" :can-write="canWrite" :images="images"
                     @changed="(msg, info) => emit('changed', msg, info)" />

      <!-- Only when the row really carries evidence: binding costs one
           authenticated fetch per picture, and a list of reports that mostly
           have none should pay nothing for them. -->
      <DefectImages v-if="defect.images?.length" :defect="defect" :items="images.items.value" />

      <dl class="facts" data-pl-part="facts">
        <template v-if="defect.suspect_paths.length">
          <dt>suspect paths</dt>
          <dd class="mono">{{ defect.suspect_paths.join(' · ') }}</dd>
        </template>
        <template v-if="defect.missing_check">
          <dt>missing check</dt>
          <dd>{{ defect.missing_check }}</dd>
        </template>
        <template v-if="defect.triage_reason">
          <dt>declined because</dt>
          <dd>{{ defect.triage_reason }}</dd>
        </template>
        <template v-if="defect.fix_state">
          <dt>fix state</dt>
          <dd class="mono">{{ defect.fix_state }}</dd>
        </template>
        <!-- The event blame was assigned against. An escape is `shipped` — one
             comparison against an immutable row, so there is no "we never
             looked" reading to render as a gap. -->
        <template v-if="defect.blamed_event_kind">
          <dt>blamed against</dt>
          <dd class="mono">{{ defect.blamed_event_kind }}</dd>
        </template>
      </dl>

      <DefectActions :defect="defect" :can-write="canWrite"
                     @changed="(msg, info) => emit('changed', msg, info)" />
    </div>
  </li>
</template>

<style scoped>
.card {
    display: flex; flex-direction: column; gap: 0.5rem;
    border: 1px solid var(--color-border); border-radius: var(--radius-xl);
    background: var(--color-surface-1); padding: 0.5rem 0.9rem;
}
[data-theme="dark"] .card { background: var(--color-slate-100); border-color: transparent; }
.defect-flat { display: flex; flex-direction: column; gap: 0.35rem; }
.head {
    display: grid; align-items: center; column-gap: 0.5rem;
    grid-template-columns: 1.5rem 5rem minmax(0, 1fr) 6.5rem minmax(0, 14rem) 5rem 1.5rem;
    min-height: 1.5rem; font-size: 11.5px; color: var(--color-fg-faint);
}
.head.has-feature {
    grid-template-columns: 1.5rem 5rem minmax(0, 1fr) minmax(0, 10rem) 6.5rem minmax(0, 14rem) 5rem 1.5rem;
}
/* A lone row has nothing to toggle, so its head is not a control. */
.head:not([tabindex]) { cursor: auto; background: none; }
.cell {
    display: flex; align-items: center; gap: 0.35rem;
    min-width: 0; overflow: hidden; white-space: nowrap;
}
.cell-id { gap: 0.3rem; }
.card .id { font-weight: 600; color: var(--color-fg); }
.cell-symptom > * { flex: none; }
.line {
    flex: 1 1 auto !important; min-width: 0;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    font-size: 13px; color: var(--color-fg);
}
.key {
    min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    font-family: var(--font-mono, monospace); font-weight: 600;
    color: var(--color-primary); text-decoration: none;
}
.key:hover { text-decoration: underline; }
.unblamed { color: var(--color-amber-800); }
.when { font-variant-numeric: tabular-nums; }
.toggle { width: 1.5rem; height: 1.5rem; }
.body { display: flex; flex-direction: column; gap: 0.5rem; cursor: auto; }
@container (max-width: 719.98px) {
    .head { grid-template-columns: 1.5rem 5rem minmax(0, 1fr) 6.5rem minmax(0, 14rem) 1.5rem; }
    .head.has-feature {
        grid-template-columns: 1.5rem 5rem minmax(0, 1fr) minmax(0, 10rem) 6.5rem minmax(0, 14rem) 1.5rem;
    }
    .when { display: none; }
}
.facts {
    display: grid; grid-template-columns: max-content 1fr;
    gap: 0.15rem 0.7rem; font-size: 11.5px;
}
.facts dt {
    font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase;
    color: var(--color-fg-faint);
}
.facts dd { margin: 0; color: var(--color-fg-muted); overflow-wrap: anywhere; }
.mono { font-family: var(--font-mono, monospace); }
</style>
