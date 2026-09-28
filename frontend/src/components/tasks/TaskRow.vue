<script setup>
// One task, on one line.
//
// Density is the point: an identifier, a priority glyph, a status glyph, the
// title, and the few facts that fit, then a strip of icon-only actions rather
// than five labelled buttons standing in every row. A backlog is read by
// scanning a column, and a three-line card with a button bar makes eight rows
// a screenful.
//
// The liveness badge is not decoration: `status` is a claim that outlives the
// process that wrote it, so a `running` task whose run is gone says so instead
// of reading as an agent still working. It says nothing at all until the
// answer arrives — silence, not an accusation.
import { computed } from 'vue'
import Badge from '../Badge.vue'
import Button from '../ui/Button.vue'
import Checkbox from '../ui/Checkbox.vue'
import Icon from '../ui/Icon.vue'
import TaskKindChip from './TaskKindChip.vue'
import TaskPriorityIcon from './TaskPriorityIcon.vue'
import TaskRefusal from './TaskRefusal.vue'
import TaskRunControls from './TaskRunControls.vue'
import TaskStatusIcon from './TaskStatusIcon.vue'
import { shortDate } from '../../utils/ledgerFormat'
import { taskRef } from '../../utils/taskMeta.js'

const props = defineProps({
  task: { type: Object, required: true },
  // What `/runs` answered about the task's newest attempt — null while the
  // question is still open, or when the task is not running at all.
  liveness: { type: Object, default: null },
  refusal: { type: Object, default: null },
  project: { type: Object, default: null },
  busy: { type: Boolean, default: false },
  selected: { type: Boolean, default: false },
  selectable: { type: Boolean, default: true },
})
defineEmits(['toggle', 'open', 'control', 'focus', 'select', 'delete'])

const archived = computed(() => props.task.status === 'archived')
const imageCount = computed(() => (props.task.images || []).length)
const answered = computed(() => props.task.status === 'running' && !!props.liveness)
const live = computed(() => !!props.liveness?.live)
const ref_ = computed(() => taskRef(props.task))
// Date only: the row has one line, and a task captured six weeks ago is not
// better understood by knowing it was 14:07. Through the shared formatter,
// because the same column on Features and Defects has to read the same (R5).
const day = computed(() => shortDate(props.task.created_at))
// How many attempts the row has, from the count the page carries. Never
// inferred from the title or the status.
const runCount = computed(() => props.task.run_count || 0)
</script>

<template>
  <div
    :id="`task-${task.id}`"
    class="task-row"
    :class="{ 'task-row-selected': selected, 'task-row-archived': archived }"
    :data-status="task.status"
    :data-feature="task.feature_id ?? undefined"
    data-testid="task-row"
  >
    <!-- No expand control: a task has attempts, not child tasks, and the
         attempts open from the row's own page. The gap keeps every row's
         checkbox on the column the group header's chevron sits in. -->
    <span class="task-expand-gap" aria-hidden="true" />

    <Checkbox
      v-if="selectable"
      class="task-select"
      :model-value="selected"
      :aria-label="`Select ${ref_}`"
      data-testid="task-select"
      @update:model-value="$emit('select', task)"
    />

    <TaskPriorityIcon class="task-priority" :value="task.priority" />

    <TaskStatusIcon :status="task.status" />

    <router-link class="task-id" :to="`/tasks/${task.id}`" data-testid="task-id">
      {{ ref_ }}
    </router-link>

    <TaskKindChip :kind="task.kind" />

    <!-- A feature step reads as the feature first: the feature title says what
         is being delivered, the task title only which step of it this is. -->
    <router-link
      v-if="task.feature_id"
      class="task-title task-title-stacked"
      :to="`/tasks/${task.id}`"
    >
      <span class="task-feature-line" data-testid="task-feature-line"
      >#{{ task.feature_id }} {{ task.feature_title }}</span>
      <span class="task-title-secondary" data-testid="task-title-secondary"
      >{{ task.title }}</span>
    </router-link>
    <router-link v-else class="task-title" :to="`/tasks/${task.id}`">
      {{ task.title }}
    </router-link>

    <div class="task-facts">
      <Button
        v-if="runCount"
        variant="ghost"
        size="sm"
        class="task-attempts"
        :aria-label="`Run history of ${ref_}`"
        data-testid="task-run-count"
        @click="$emit('open', 'runs', task)"
      >
        {{ runCount === 1 ? '1 attempt' : `${runCount} attempts` }}
      </Button>
      <Badge
        v-if="answered"
        :color="live ? 'green' : 'red'"
        :label="live ? 'live' : 'not live'"
        :data-testid="live ? 'task-live' : 'task-not-live'"
      />
      <Badge
        v-if="project"
        color="cyan"
        :label="project.name"
        data-testid="task-project"
      />
      <Badge v-for="tag in task.tags" :key="tag" color="purple" :label="tag" />
      <!-- A count, not thumbnails: a 50-row page would otherwise pull one
           authenticated blob per image just to render the list. -->
      <Badge
        v-if="imageCount"
        color="blue"
        :label="imageCount === 1 ? '1 image' : `${imageCount} images`"
        data-testid="task-image-count"
      />
    </div>

    <!-- Its own column, not the tail of the badge row: a date that starts
         wherever the tags happened to end cannot be scanned down. -->
    <span class="task-day">{{ day }}</span>

    <div class="task-actions">
      <TaskRunControls
        :task="task"
        :liveness="liveness"
        :busy="busy"
        @control="action => $emit('control', task, action)"
      />
      <Button
        variant="ghost"
        size="sm"
        :aria-label="`Edit ${ref_}`"
        data-testid="task-edit"
        @click="$emit('open', 'edit', task)"
      >
        <Icon name="pencil" :size="13" />
      </Button>
      <Button
        v-if="!archived"
        variant="ghost"
        size="sm"
        :aria-label="`Arm ${ref_}`"
        data-testid="task-arm"
        @click="$emit('open', 'arm', task)"
      >
        <Icon name="power" :size="13" />
      </Button>
      <Button
        variant="ghost"
        size="sm"
        :aria-label="`Dependencies of ${ref_}`"
        data-testid="task-deps"
        @click="$emit('open', 'deps', task)"
      >
        <Icon name="workflow" :size="13" />
      </Button>
      <Button
        variant="ghost"
        size="sm"
        :aria-label="`Run history of ${ref_}`"
        data-testid="task-runs-open"
        @click="$emit('open', 'runs', task)"
      >
        <Icon name="list" :size="13" />
      </Button>
      <Button
        variant="ghost"
        size="sm"
        :aria-label="`${archived ? 'Restore' : 'Archive'} ${ref_}`"
        @click="$emit('toggle', task)"
      >
        <Icon :name="archived ? 'refresh-cw' : 'archive'" :size="13" />
      </Button>
      <Button
        variant="ghost"
        size="sm"
        :aria-label="`Delete ${ref_}`"
        data-testid="task-delete"
        @click="$emit('delete', task)"
      >
        <Icon name="trash" :size="13" />
      </Button>
    </div>
  </div>

  <p v-if="answered && !live" class="task-stale" data-testid="task-stale">
    {{ ref_ }} — nothing is behind this run; the runner is gone, so it can be
    started again.
  </p>
  <TaskRefusal
    v-if="refusal"
    :refusal="refusal"
    @focus="id => $emit('focus', id)"
  />
</template>

<style scoped>
.task-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.4375rem 0.75rem;
    border-bottom: 1px solid var(--color-border-subtle);
    background: var(--color-surface);
    min-width: 0;
}
.task-row:hover { background: var(--color-surface-2); }
.task-row-selected { background: var(--color-surface-selected); }
.task-row-archived .task-title { color: var(--color-fg-subtle); }

.task-select { flex-shrink: 0; }
.task-priority { flex-shrink: 0; }
.task-expand-gap { flex: 0 0 1.5rem; }

.task-attempts {
    height: auto;
    font-size: 0.6875rem;
    padding: 0.0625rem 0.375rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-full, 999px);
    color: var(--color-fg-muted);
    font-variant-numeric: tabular-nums;
}

.task-id {
    flex-shrink: 0;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.75rem;
    font-variant-numeric: tabular-nums;
    color: var(--color-fg-faint);
    text-decoration: none;
}
.task-id:hover { color: var(--color-fg-muted); }

.task-title {
    flex: 1 1 auto;
    min-width: 0;
    font-size: 0.8125rem;
    font-weight: 500;
    color: var(--color-fg);
    text-decoration: none;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.task-title:hover { color: var(--color-primary); }
.task-title-stacked {
    display: flex;
    flex-direction: column;
    line-height: 1.25;
}
.task-feature-line,
.task-title-secondary {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.task-title-secondary {
    font-size: 0.6875rem;
    font-weight: 400;
    color: var(--color-fg-muted);
}

.task-facts {
    display: flex;
    align-items: center;
    gap: 0.25rem;
    flex-shrink: 0;
}
.task-day {
    flex: 0 0 5.25rem;
    text-align: right;
    font-size: 0.6875rem;
    color: var(--color-fg-faint);
    font-variant-numeric: tabular-nums;
}

/* Always drawn: an action reachable only on hover is one a touch screen or a
   scanning eye never finds (feature #119 I2). */
.task-actions {
    display: flex;
    align-items: center;
    gap: 0.125rem;
    flex-shrink: 0;
}

.task-stale {
    padding: 0 0.75rem 0.5rem 2.5rem;
    font-size: 0.75rem;
    color: var(--color-warning);
    background: var(--color-surface);
    overflow-wrap: anywhere;
}

/* Narrow: the title is the row, so it gets the whole first line and the
   actions wrap onto a second one. Keeping them inline instead crushes a flex
   title to a single ellipsized character — which is the one thing on the row
   nobody can do without. */
@media (max-width: 40rem) {
    .task-row { flex-wrap: wrap; }
    .task-facts,
    .task-day { display: none; }
    .task-actions {
        flex: 1 0 100%;
        justify-content: flex-end;
    }
}
</style>
