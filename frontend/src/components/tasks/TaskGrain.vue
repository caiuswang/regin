<script setup>
// Which grain this task is, and the rows on the other side of it.
//
// A task with `parent_id === null` that something names as parent IS a
// feature, and its children are the runs. The two directions render as two
// different things on purpose: a feature lists its runs, because that list is
// the evidence behind its ledger, while a run carries one link back, because
// the feature is context rather than content.
//
// A task that is neither carries no run list — but it does carry the ACT, for
// an editor: a task becomes a feature the moment something is named a run of
// it, and that declaration had no browser surface at all. The card with no
// rows asserts no structure; it offers the one thing that would create it.
import { computed, onMounted, ref, watch } from 'vue'
import api from '../../api'
import Badge from '../Badge.vue'
import Button from '../ui/Button.vue'
import Card from '../Card.vue'
import Icon from '../ui/Icon.vue'
import TaskAttachRunDialog from './TaskAttachRunDialog.vue'
import { taskStatusColor } from '../../composables/useBadgeColor.js'

const props = defineProps({
  task: { type: Object, required: true },
  // Served by the detail route. The list is fetched only when this is
  // non-zero: a page with an address must resolve it directly, and asking the
  // feed "are there children?" would make every task detail scan a page to
  // find out that most of them have none.
  childCount: { type: Number, default: 0 },
  // The task's own attempts. A top-level task holding some is the feature
  // they built, under its own key.
  runCount: { type: Number, default: 0 },
  canWrite: { type: Boolean, default: false },
})
const emit = defineEmits(['attached'])

const children = ref([])
const loading = ref(false)

const isChild = computed(() => props.task.parent_id !== null)
const hasChildren = computed(() => children.value.length > 0)
const ownKey = computed(() => `regin:${props.task.id}`)
const parentKey = computed(() => `regin:${props.task.parent_id}`)

async function load() {
  if (isChild.value || props.childCount === 0) {
    children.value = []
    return
  }
  loading.value = true
  try {
    const data = await api.get(`/tasks?parent_id=${props.task.id}&limit=100`)
    children.value = data.tasks || data.items || []
  } finally {
    loading.value = false
  }
}

const attaching = ref(false)

function onAttached() {
  attaching.value = false
  emit('attached')
}

watch(() => [props.task.id, props.childCount], load)
onMounted(load)
defineExpose({ reload: load })
</script>

<template>
  <!-- A run, pointing back at the feature it is a run of. -->
  <Card v-if="isChild" data-testid="task-parent">
    <h2 class="card-header">A run of</h2>
    <router-link class="tg-parent" :to="`/tasks/${task.parent_id}`">
      <Icon name="chevron-left" :size="13" />
      task #{{ task.parent_id }}
    </router-link>
    <p class="tg-note">
      This task's runs are counted as sessions of that feature, and its cost is
      charged there —
      <router-link
        class="tg-ledger"
        :to="`/features/${parentKey}`"
        data-testid="task-parent-ledger"
      >its ledger</router-link>.
    </p>
  </Card>

  <!-- A feature, listing its runs — or a task standing where one would be
       declared. -->
  <Card v-else-if="hasChildren || canWrite" data-testid="task-runs">
    <div class="tg-head">
      <h2 class="card-header tg-title">
        {{ runCount && !hasChildren ? `Attempts (${runCount})` : `Runs (${children.length})` }}
      </h2>
      <Button
        v-if="canWrite && !runCount"
        variant="ghost"
        size="sm"
        data-testid="task-attach-run"
        @click="attaching = true"
      >
        <Icon name="plus" :size="13" />
        <span>Attach a run</span>
      </Button>
    </div>
    <ul v-if="hasChildren" class="tg-list">
      <li v-for="child in children" :key="child.id" class="tg-item">
        <router-link class="tg-link" :to="`/tasks/${child.id}`">
          #{{ child.id }} {{ child.title }}
        </router-link>
        <Badge :color="taskStatusColor(child.status)" :label="child.status" />
      </li>
    </ul>
    <p v-if="hasChildren" class="tg-note">
      This task is a feature. Its status follows these runs and is never set
      directly — except by the done-claim, which is an operator act.
      <router-link
        class="tg-ledger"
        :to="`/features/${ownKey}`"
        data-testid="task-feature-ledger"
      >Read its ledger</router-link>.
    </p>
    <p v-else-if="runCount" class="tg-note" data-testid="task-own-runs">
      This task is a feature: its {{ runCount === 1 ? 'attempt is' : `${runCount} attempts are` }}
      its sessions, under <code>{{ ownKey }}</code>.
      <router-link
        class="tg-ledger"
        :to="`/features/${ownKey}`"
        data-testid="task-feature-ledger"
      >Read its ledger</router-link>.
    </p>
    <p v-else class="tg-note" data-testid="task-runs-empty">
      Nothing is a run of this task yet. It becomes a feature — with a ledger
      of its own — the first time it is run, or when something is named a run
      of it.
    </p>
  </Card>

  <p v-else-if="loading" class="tg-loading">reading runs…</p>

  <TaskAttachRunDialog
    v-if="attaching"
    :task="task"
    @close="attaching = false"
    @attached="onAttached"
  />
</template>

<style scoped>
.tg-parent {
    display: inline-flex; align-items: center; gap: 0.2rem;
    font-family: var(--font-mono, monospace); font-size: 0.8125rem;
    font-weight: 600; color: var(--color-primary); text-decoration: none;
}
.tg-parent:hover { text-decoration: underline; }
.tg-head {
    display: flex; align-items: baseline; justify-content: space-between;
    gap: 0.5rem;
}
.tg-title { margin-bottom: 0; }
.tg-list { list-style: none; margin: 0; padding: 0;
           display: flex; flex-direction: column; gap: 0.3rem; }
.tg-item { display: flex; align-items: center; gap: 0.5rem; min-width: 0; }
.tg-link {
    flex: 1; min-width: 0; font-size: 0.8125rem;
    color: var(--color-fg); text-decoration: none; overflow-wrap: anywhere;
}
.tg-link:hover { text-decoration: underline; }
.tg-note {
    margin: 0.5rem 0 0; font-size: 11.5px; color: var(--color-fg-subtle);
}
.tg-loading { font-size: 0.8125rem; color: var(--color-fg-subtle); }
.tg-ledger { color: var(--color-primary); text-decoration: none; }
.tg-ledger:hover { text-decoration: underline; }
</style>
