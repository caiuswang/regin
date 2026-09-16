<script setup>
// Which feature this task is a step of.
//
// One nullable column, so this card answers a question with exactly one
// answer. It replaced three: a task held its feature through a tracker link,
// through the parent it was a run of, or through its own id, and the surfaces
// that rendered those disagreed about which counted.
//
// **The feature's own title, never the task's.** They answer different
// questions — `features.title` says what is being delivered, a task's says
// what this step does — and they diverge from the moment both are edited.
//
// The done-claim is NOT here. It is an act on the feature, made on the
// feature's own page: a task's status is its execution lifecycle, and the two
// sharing a control is what let a settling run ship a feature nobody claimed.
import { computed, ref } from 'vue'
import api from '../../api'
import Badge from '../Badge.vue'
import Button from '../ui/Button.vue'
import Card from '../Card.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'
import { STAGE_COLOR } from '../../utils/ledgerFormat'

const props = defineProps({
  taskId: { type: Number, required: true },
  feature: { type: Object, default: null },
  canWrite: { type: Boolean, default: false },
})
const emit = defineEmits(['changed'])

const picking = ref(false)
const wanted = ref('')
const busy = ref(false)
const failed = ref('')

const stageColor = computed(
  () => STAGE_COLOR[props.feature?.stage] || 'gray')

// An UPDATE of one column, whichever direction it goes — there is no second
// row for a second feature to be written into, which is why this needs no
// guard and offers no merge.
async function place(featureId) {
  busy.value = true
  failed.value = ''
  const body = await api.patch(`/tasks/${props.taskId}`,
                               { feature_id: featureId })
  busy.value = false
  if (body?.ok === false) {
    failed.value = body.msg || 'that feature was refused'
    return
  }
  picking.value = false
  wanted.value = ''
  emit('changed', body.feature)
}
</script>

<template>
  <Card>
    <header class="tf-head">
      <h2 class="card-header">Feature</h2>
      <Button
        v-if="canWrite && !picking"
        variant="ghost" size="sm"
        data-testid="task-place-feature"
        @click="picking = true"
      >
        <Icon name="pencil" :size="12" />
        {{ feature ? 'Move' : 'Place' }}
      </Button>
    </header>

    <div v-if="feature" class="tf-row">
      <router-link class="tf-title" :to="`/features/${feature.id}`"
      >{{ feature.title }}</router-link>
      <Badge :color="stageColor" :label="feature.stage" />
      <span v-if="feature.external_ref" class="tf-ref"
      >{{ feature.external_ref }}</span>
    </div>

    <!-- A real state, not a gap: a task nobody has placed under a feature is
         work somebody captured and has not yet decided the shape of. -->
    <p v-else class="tf-none" data-testid="task-no-feature">
      Under no feature. Its runs are attributed to nothing, and a defect
      reported here would have nothing to blame.
    </p>

    <form v-if="picking" class="tf-pick" @submit.prevent="place(Number(wanted))">
      <Input
        v-model="wanted"
        class="tf-input"
        placeholder="feature id"
        aria-label="The id of the feature this task is a step of"
      />
      <Button type="submit" size="sm" :disabled="busy || !wanted">Place</Button>
      <Button
        v-if="feature"
        variant="ghost" size="sm" :disabled="busy"
        @click="place(null)"
      >Detach</Button>
      <Button variant="ghost" size="sm" @click="picking = false">Cancel</Button>
    </form>

    <p v-if="failed" class="tf-fail" role="alert">{{ failed }}</p>
  </Card>
</template>

<style scoped>
.tf-head {
    display: flex; align-items: baseline; justify-content: space-between;
    gap: 0.5rem;
}
.tf-row {
    display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;
}
.tf-title {
    font-weight: 600; font-size: 0.9rem; color: var(--color-fg);
    text-decoration: none;
}
.tf-title:hover { text-decoration: underline; }
.tf-title:focus-visible { outline: 2px solid var(--color-blue-500); }
.tf-ref {
    font-family: var(--font-mono, monospace); font-size: 11px;
    color: var(--color-fg-subtle);
}
.tf-none { font-size: 12px; color: var(--color-fg-subtle); }
.tf-pick {
    display: flex; align-items: center; gap: 0.4rem; margin-top: 0.6rem;
    flex-wrap: wrap;
}
.tf-input { width: 9rem; }
.tf-fail {
    margin-top: 0.4rem; font-size: 11.5px; color: var(--color-red-700);
}
</style>
