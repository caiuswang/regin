<script setup>
// The four axes the feed has always understood and the toolbar never offered:
// tag, source, feature and assignee. They live in a popover rather than in the
// resting toolbar because they narrow to one thread of work — you reach for
// them knowing the tag or the feature — while status, priority and project are
// how the backlog is read every day.
//
// Every one of them is a URL axis like the rest, so a narrowing reached from
// here is as shareable as one reached from the bar.
import { computed, ref } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'
import Popover from '../ui/Popover.vue'
import Select from '../ui/Select.vue'

const props = defineProps({
  state: { type: Object, required: true },
  users: { type: Array, default: () => [] },
})
const emit = defineEmits(['patch'])

const open = ref(false)

// `source` is an open vocabulary — `lib/tasks/capture.py` accepts any
// `[a-z0-9_-]+`, so cli, linear, defect and whatever integration lands next
// are all legal. A Select over a fixed list would quietly hide the rest.
const draft = ref({ tag: '', source: '', feature_id: '', assignee: '' })

const userOptions = computed(() => [
  { value: '', label: 'Anyone' },
  ...props.users.map(u => ({ value: String(u.id), label: u.name })),
])

const count = computed(() => ['tag', 'source', 'feature_id', 'assignee']
  .filter(k => props.state[k]).length)

function show() {
  draft.value = {
    tag: props.state.tag,
    source: props.state.source,
    feature_id: props.state.feature_id,
    assignee: props.state.assignee,
  }
  open.value = true
}

// Applied on submit, not per keystroke: each of these is a URL write, and a
// history entry per character is what a debounce would otherwise be invented
// to paper over.
function apply() {
  emit('patch', { ...draft.value })
  open.value = false
}

function clear() {
  draft.value = { tag: '', source: '', feature_id: '', assignee: '' }
  emit('patch', { ...draft.value })
  open.value = false
}
</script>

<template>
  <Popover v-model:open="open" align="start">
    <template #trigger>
      <Button
        variant="ghost"
        size="sm"
        :aria-expanded="open"
        aria-label="More filters"
        data-testid="task-more-filters"
        @click="show()"
      >
        <Icon name="filter" :size="13" />
        <span>More</span>
        <span v-if="count" class="tmf-count" data-testid="task-more-count">{{ count }}</span>
      </Button>
    </template>

    <form class="tmf" @submit.prevent="apply()">
      <label class="tmf-field">
        <span class="tmf-label">Tag</span>
        <Input
          v-model="draft.tag"
          type="search"
          placeholder="release"
          data-testid="task-filter-tag"
        />
      </label>
      <label class="tmf-field">
        <span class="tmf-label">Source</span>
        <Input
          v-model="draft.source"
          type="search"
          placeholder="cli, linear, defect…"
          data-testid="task-filter-source"
        />
      </label>
      <label class="tmf-field">
        <span class="tmf-label">Feature</span>
        <Input
          v-model="draft.feature_id"
          type="number"
          min="1"
          placeholder="73"
          data-testid="task-filter-feature"
        />
      </label>
      <label class="tmf-field">
        <span class="tmf-label">Assignee</span>
        <Select
          v-model="draft.assignee"
          :options="userOptions"
          data-testid="task-filter-assignee"
        />
      </label>

      <div class="tmf-acts">
        <Button
          v-if="count"
          variant="ghost"
          size="sm"
          type="button"
          data-testid="task-more-clear"
          @click="clear()"
        >
          Clear these
        </Button>
        <Button variant="primary" size="sm" type="submit" data-testid="task-more-apply">
          Apply
        </Button>
      </div>
    </form>
  </Popover>
</template>

<style scoped>
.tmf {
    display: flex;
    flex-direction: column;
    gap: 0.625rem;
    min-width: 15rem;
}
.tmf-field {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
}
.tmf-label {
    font-size: 0.6875rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-fg-subtle);
}
.tmf-acts {
    display: flex;
    justify-content: flex-end;
    gap: 0.375rem;
    padding-top: 0.125rem;
}
.tmf-count {
    margin-left: 0.25rem;
    padding: 0 0.3rem;
    border-radius: var(--radius-full, 999px);
    background: var(--color-surface-3);
    font-size: 0.6875rem;
    font-variant-numeric: tabular-nums;
}
</style>
