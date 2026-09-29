<script setup>
// One collapsible group, owning its own keyset feed.
//
// A group pages independently rather than being a slice of one big load: a
// collapsed group costs nothing, and an open one can be 4 000 rows deep
// without the other groups paying for it. The header count is the server's —
// counting the loaded rows would report the size of the page, and a collapsed
// group would report zero.
import { computed, onUnmounted, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import TaskRows from './TaskRows.vue'
import { useTaskFeed } from '../../composables/useTasks.js'

const props = defineProps({
  group: { type: Object, required: true },
  params: { type: Object, required: true },
  liveness: { type: Object, default: () => ({}) },
  refusals: { type: Object, default: () => ({}) },
  selection: { type: Set, default: () => new Set() },
  busyId: { type: Number, default: null },
  projectFor: { type: Function, default: () => null },
  canWrite: { type: Boolean, default: true },
  dateField: { type: String, default: 'created_at' },
})
const emit = defineEmits(['verb', 'open', 'focus', 'select', 'loaded', 'hidden'])

// Whether the group is open is the LIST's state, not this component's: it has
// to outlive the group's unmount to survive a filter change or a tab switch,
// and a header that re-opens on every mount is what made a collapsed group
// cost a request anyway.
const open = defineModel('open', { type: Boolean, default: true })

const scoped = computed(() => ({ ...props.params.value,
                                 [props.group.param]: props.group.key }))
const feed = useTaskFeed(computed(() => scoped.value), () => ({}))

const fetched = ref(false)

async function load() {
  await feed.load()
  fetched.value = true
  emit('loaded', props.group.key, feed.items.value)
}

// The fetch is caused by the group becoming visible, never by a timer — and a
// re-open does not refetch what is already held. Closing withdraws the rows it
// was rendering: they are no longer on screen, so nothing that counts or acts
// on what is on screen may still see them.
watch(open, (isOpen) => {
  if (!isOpen) { emit('hidden', props.group.key); return }
  if (!fetched.value) load()
  else emit('loaded', props.group.key, feed.items.value)
}, { immediate: true })

onUnmounted(() => emit('hidden', props.group.key))

// A second page is more rows on screen, so what is on screen has to be
// restated — otherwise select-all keeps describing only the first page.
async function more() {
  await feed.loadMore()
  emit('loaded', props.group.key, feed.items.value)
}

// A filter change invalidates what was fetched under the old one. The LIST
// drives that, not a watcher here: `scoped` changes on exactly the params
// change the list already reloads on, so watching it too issued every open
// group's page twice and let two in-flight loads race over one `items`.
defineExpose({ reload: () => { fetched.value = false; if (open.value) load() } })
</script>

<template>
  <section class="tg" :data-testid="`task-group-${group.key}`">
    <Button
      variant="ghost"
      class="tg-head"
      :aria-expanded="open"
      :aria-label="`${open ? 'Collapse' : 'Expand'} ${group.label}`"
      :data-testid="`task-group-toggle-${group.key}`"
      @click="open = !open"
    >
      <Icon :name="open ? 'chevron-down' : 'chevron-right'" :size="14" />
      <span class="tg-label">{{ group.label }}</span>
      <span class="tg-count" :data-testid="`task-group-count-${group.key}`">
        {{ group.count }}
      </span>
    </Button>

    <div v-if="open" class="tg-body">
      <div v-if="feed.loading.value" class="tg-note">Loading…</div>
      <div v-else-if="feed.error.value" class="tg-note" role="alert">
        {{ feed.error.value }}
      </div>
      <div v-else-if="!feed.items.value.length" class="tg-note">
        Nothing here.
      </div>
      <template v-else>
        <TaskRows
          :tasks="feed.items.value"
          :liveness="liveness"
          :refusals="refusals"
          :selection="selection"
          :busy-id="busyId"
          :project-for="projectFor"
          :can-write="canWrite"
          :date-field="dateField"
          @verb="(v, t) => emit('verb', v, t)"
          @open="(kind, t) => emit('open', kind, t)"
          @focus="id => emit('focus', id)"
          @select="t => emit('select', t)"
        />
        <div v-if="feed.hasNext.value" class="tg-more">
          <Button
            variant="ghost"
            size="sm"
            :disabled="feed.loadingMore.value"
            :data-testid="`task-group-more-${group.key}`"
            @click="more()"
          >
            {{ feed.loadingMore.value ? 'Loading…'
              : `Load more (${group.count - feed.items.value.length} left)` }}
          </Button>
        </div>
      </template>
    </div>
  </section>
</template>

<style scoped>
.tg {
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    background: var(--color-surface);
    overflow: hidden;
}
.tg-head {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    width: 100%;
    height: auto;
    padding: 0.5rem 0.75rem;
    background: var(--color-surface-2);
    border-radius: 0;
    border-bottom: 1px solid var(--color-border-subtle);
    color: var(--color-fg);
    justify-content: flex-start;
}
.tg-head:hover { background: var(--color-surface-3); }
.tg-label { font-size: 0.8125rem; font-weight: 600; }
.tg-count {
    font-size: 0.75rem;
    color: var(--color-fg-subtle);
    font-variant-numeric: tabular-nums;
}
.tg-body :deep(.task-row:last-child) { border-bottom: 0; }
.tg-note {
    padding: 0.75rem;
    font-size: 0.8125rem;
    color: var(--color-fg-subtle);
}
.tg-more {
    display: flex;
    justify-content: center;
    padding: 0.5rem;
    border-top: 1px solid var(--color-border-subtle);
}
</style>
