<script setup>
// One comment's rendered body, with the images it was posted with.
//
// `image:N` in a comment names that comment's own images and nothing else —
// not the task's, not a neighbour's in the same thread — because each
// comment's idx sequence starts at 1. So the lookup table handed to
// `resolveImageRefs` is built from this comment alone. An image the body does
// not cite is still the comment's, and is shown beneath it as an attachment.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import api from '../../api'
import MarkdownContent from '../MarkdownContent.vue'
import { imageRefsIn, resolveImageRefs, splitImageRefs } from '../../utils/taskBodyImages'

const props = defineProps({
  comment: { type: Object, required: true },
  project: { type: Number, default: null },
})

const items = ref([])
const ready = ref(false)
let generation = 0

function release() {
  for (const item of items.value) URL.revokeObjectURL(item.url)
  items.value = []
}

async function load() {
  generation += 1
  const mine = generation
  release()
  const declared = props.comment.images || []
  ready.value = !declared.length
  const fetched = await Promise.all(declared.map((img) =>
    api.getBlobUrl(`/comments/${props.comment.id}/images/${img.idx}`)
      .then((url) => ({ idx: img.idx, url }))
      .catch(() => null)))
  const got = fetched.filter(Boolean)
  if (mine !== generation) {
    for (const item of got) URL.revokeObjectURL(item.url)
    return
  }
  items.value = got
  ready.value = true
}

watch(() => [props.comment.id, (props.comment.images || []).map((i) => i.idx).join(',')], load,
      { immediate: true })
onBeforeUnmount(() => { generation += 1; release() })

const body = computed(() => props.comment.body || '')
// Until the bytes arrive a cited image is left out rather than drawn as
// "no longer attached", which is what an unknown idx resolves to.
const shown = computed(() => (ready.value
  ? resolveImageRefs(body.value, items.value)
  : splitImageRefs(body.value).map((part) => part.text ?? '').join('')))
const attachments = computed(() => {
  const cited = imageRefsIn(body.value)
  return items.value.filter((item) => !cited.has(item.idx))
})
</script>

<template>
  <div class="tcb">
    <MarkdownContent
      v-if="body"
      class="tcb-text" :markdown="shown" refs :project="project"
    />
    <div v-if="attachments.length" class="tcb-attachments" data-testid="task-comment-attachments">
      <a
        v-for="item in attachments" :key="item.idx"
        :href="item.url" target="_blank" rel="noopener"
        class="tcb-attachment focus-visible:outline-2 focus-visible:outline-offset-2"
        :data-idx="item.idx"
        data-testid="task-comment-attachment"
      ><img :src="item.url" :alt="`image ${item.idx}`"></a>
    </div>
  </div>
</template>

<style scoped>
.tcb { min-width: 0; }
/* A comment is a row, so its rendered blocks may not add outer space; the
   gap between comments belongs to the row, not to the markdown. */
.tcb-text :deep(> :first-child) { margin-top: 0; }
.tcb-text :deep(> :last-child) { margin-bottom: 0; }
.tcb-text :deep(img) { max-width: 100%; height: auto; border-radius: var(--radius-md); }
.tcb-attachments { display: flex; flex-wrap: wrap; gap: 0.375rem; margin-top: 0.375rem; }
.tcb-attachment {
    display: block; width: 4.5rem; height: 4.5rem; overflow: hidden;
    border: 1px solid var(--color-border); border-radius: var(--radius-md);
}
.tcb-attachment img { width: 100%; height: 100%; object-fit: cover; display: block; }
</style>
