<script setup>
// The spec the feature was declared with, read back and corrected in place.
//
// `features.body` was write-only from the browser: the declare dialog
// collected it, the create endpoint stored it, and no read ever served it
// again. A spec nobody can read is a spec nobody can check a build against,
// which is the one job the acceptance list has.
//
// Edited as source beside a live preview (`editor/MarkdownEditor` says why
// source-first): the editor hands back the exact bytes it was given. A save
// appends no event — rounds count builds, and
// a reworded sentence is not one.
//
// Images work the way they do in a task's body, and for the same reason: a
// paste attaches the bytes AND writes the `![](image:N)` reference at the
// caret. The syntax is otherwise unguessable, and an attachment the prose
// cannot name is a picture beside the spec rather than a picture in it.
//
// A tracked feature's spec lives in its issue, and Refresh overwrites
// `features.body` with the issue's text — so an edit here would be lost on the
// next refresh. The card is read-only there and points at the tracker instead;
// the stage line's Refresh is what pulls the issue's text back in.
import { computed, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Card from '../Card.vue'
import Icon from '../ui/Icon.vue'
import MarkdownContent from '../MarkdownContent.vue'
import MarkdownEditor from '../editor/MarkdownEditor.vue'
import { caretTarget, withCaretInsert } from '../../utils/caret'
import { imageFilesFrom } from '../../utils/imageFiles'
import { SPEC_ANCHOR } from '../../utils/featureLinks'
import { IMAGE_REF_HINT, imageRefBlock, resolveImageRefs } from '../../utils/taskBodyImages'

const props = defineProps({
  featureId: { type: Number, required: true },
  body: { type: String, default: null },
  // The ledger view's list, shared with the Images card — never a second one.
  images: { type: Array, default: () => [] },
  canWrite: { type: Boolean, default: false },
  // Returns the `idx` of every image it attached, which is what the reference
  // written back into the text is made of.
  attach: { type: Function, default: null },
  tracked: { type: Boolean, default: false },
  tracker: { type: String, default: '' },
  externalUrl: { type: String, default: null },
  // The feature's project: what an `@` in the spec searches first.
  project: { type: Number, default: null },
})
const emit = defineEmits(['saved'])

const trackerLabel = computed(() => {
  const name = props.tracker || 'the tracker'
  return name.charAt(0).toUpperCase() + name.slice(1)
})

const editing = ref(false)
const draft = ref('')
const saving = ref(false)
const error = ref('')
const area = ref(null)

const rendered = computed(() => resolveImageRefs(props.body, props.images))

function start() {
  draft.value = props.body || ''
  error.value = ''
  editing.value = true
}

async function save() {
  saving.value = true
  // The server's own sentence, not a reworded one: `set_body` names the limit
  // it refused on, and the spec is the only field in flight here.
  const res = await api.patch(`/features/${props.featureId}`, { body: draft.value })
  saving.value = false
  if (res?.ok === false) {
    error.value = res.msg || 'could not save the spec'
    return
  }
  editing.value = false
  // The parent re-seats the whole ledger from the response, so what the page
  // shows after a save is what the server stored — never the local draft.
  emit('saved', res)
}

// A failed attach writes nothing into the draft — no placeholder for a picture
// that never arrived — and says why under the field the paste landed in.
async function takeImages(source, event) {
  error.value = ''
  let taken
  try {
    taken = await props.attach(source)
  } catch (e) {
    error.value = e?.message || 'could not attach the image'
    return
  }
  const refs = imageRefBlock(taken)
  if (!refs) {
    error.value = 'could not attach the image'
    return
  }
  draft.value = withCaretInsert(caretTarget(event, area.value), draft.value, refs)
}

async function onPaste(e) {
  if (!props.attach || !imageFilesFrom(e.clipboardData).length) return
  e.preventDefault()
  await takeImages(e.clipboardData, e)
}

// Drop is paste's neighbour and reaches the files a screenshot tool wrote to
// disk rather than to the clipboard. It has no caret of its own, so the
// reference lands at wherever the textarea was last left.
async function onDrop(e) {
  if (!props.attach || !imageFilesFrom(e.dataTransfer).length) return
  e.preventDefault()
  await takeImages(e.dataTransfer, e)
}
</script>

<template>
  <Card :id="SPEC_ANCHOR" data-testid="feature-spec">
    <div class="fs-head">
      <div>
        <h2 class="card-header fs-title">Spec</h2>
        <p class="fs-sub">what this feature was declared to deliver</p>
      </div>
      <div v-if="tracked" class="fs-actions" data-testid="feature-spec-tracked">
        <a
          v-if="externalUrl"
          class="fs-tracker-link focus-visible:outline-2 focus-visible:outline-blue-500"
          :href="externalUrl" target="_blank" rel="noopener"
          data-testid="feature-spec-tracker-link"
        >Edit in {{ trackerLabel }} →</a>
        <span v-else class="fs-sub">read-only — edit it in {{ trackerLabel }}</span>
      </div>
      <Button
        v-else-if="!editing && canWrite"
        variant="ghost" size="sm" data-testid="feature-spec-edit" @click="start"
      >
        <Icon name="pencil" :size="13" />
        <span>Edit</span>
      </Button>
      <div v-else-if="editing" class="fs-actions">
        <Button
          variant="primary" size="sm" :disabled="saving"
          data-testid="feature-spec-save" @click="save"
        >{{ saving ? 'Saving…' : 'Save' }}</Button>
        <Button
          variant="secondary" size="sm" :disabled="saving"
          data-testid="feature-spec-cancel" @click="editing = false"
        >Cancel</Button>
      </div>
    </div>

    <template v-if="editing && !tracked">
      <MarkdownEditor
        ref="area"
        v-model="draft" :project="project" :feature="featureId" :images="images" :attach="attach"
        preview-testid="feature-spec-preview-pane"
        :rows="14" spellcheck="false"
        aria-label="Feature spec markdown" data-testid="feature-spec-input"
        @paste="onPaste"
        @dragover.prevent
        @drop="onDrop"
      />
      <p class="fs-hint">
        Markdown. {{ IMAGE_REF_HINT }} Paste or drop an image here.
        Editing it appends no event.
      </p>
      <p v-if="error" class="fs-error" role="alert" data-testid="feature-spec-error">
        {{ error }}
      </p>
    </template>

    <template v-else>
      <p v-if="tracked" class="fs-hint" data-testid="feature-spec-tracked-hint">
        The text of the {{ trackerLabel }} issue. Refresh on the stage line pulls it again.
      </p>
      <MarkdownContent
        v-if="body" class="fs-body" :markdown="rendered" refs :project="project"
        data-testid="feature-spec-body"
      />
      <p v-else class="fs-empty" data-testid="feature-spec-empty">
        Declared with no spec. Without one there is nothing for the acceptance
        list to be checked against.
      </p>
    </template>
  </Card>
</template>

<style scoped>
.fs-head {
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: 0.75rem;
}
.fs-title { margin-bottom: 0; }
.fs-sub { font-size: 11.5px; color: var(--color-fg-subtle); }
.fs-actions { display: flex; align-items: center; gap: 0.375rem; }
.fs-tracker-link { font-size: 12px; color: var(--color-primary); text-decoration: none; }
.fs-tracker-link:hover { text-decoration: underline; }
.fs-hint { margin-top: 0.375rem; font-size: 0.6875rem; color: var(--color-fg-faint); }
.fs-error {
    margin-top: 0.375rem; font-size: 0.8125rem; color: var(--color-danger);
    overflow-wrap: anywhere;
}
.fs-body {
    margin-top: 0.75rem; font-size: 0.8125rem; color: var(--color-fg-muted);
    overflow-wrap: anywhere;
}
.fs-body :deep(img) {
    max-width: 100%; height: auto; border-radius: var(--radius-md);
}
.fs-empty {
    margin-top: 0.75rem; font-size: 11.5px; color: var(--color-fg-muted);
}
</style>
