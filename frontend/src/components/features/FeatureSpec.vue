<script setup>
// The spec the feature was declared with, read back and corrected in place.
//
// `features.body` was write-only from the browser: the declare dialog
// collected it, the create endpoint stored it, and no read ever served it
// again. A spec nobody can read is a spec nobody can check a build against,
// which is the one job the acceptance list has.
//
// Source over a rendered editor, for the reason `TaskBodyEditor` states: the
// column holds prose agents both read and write, so the editor hands back the
// exact bytes it was given. A save appends no event — rounds count builds, and
// a reworded sentence is not one.
//
// Images work the way they do in a task's body, and for the same reason: a
// paste attaches the bytes AND writes the `![](image:N)` reference at the
// caret. The syntax is otherwise unguessable, and an attachment the prose
// cannot name is a picture beside the spec rather than a picture in it.
import { computed, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Card from '../Card.vue'
import Icon from '../ui/Icon.vue'
import MarkdownContent from '../MarkdownContent.vue'
import Textarea from '../ui/Textarea.vue'
import { imageFilesFrom } from '../../utils/imageFiles'
import { IMAGE_REF_HINT, imageRef, resolveImageRefs } from '../../utils/taskBodyImages'

const props = defineProps({
  featureId: { type: Number, required: true },
  body: { type: String, default: null },
  // The ledger view's list, shared with the Images card — never a second one.
  images: { type: Array, default: () => [] },
  canWrite: { type: Boolean, default: false },
  // Returns the `idx` of every image it attached, which is what the reference
  // written back into the text is made of.
  attach: { type: Function, default: null },
})
const emit = defineEmits(['saved'])

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

/** Put `text` where the caret is, so a pasted image lands where it was pasted. */
function insertAtCaret(text) {
  // `Textarea` is a component, so the caret lives on its root element.
  const el = area.value?.$el ?? area.value
  if (!el) { draft.value += text; return }
  const at = el.selectionStart ?? draft.value.length
  draft.value = draft.value.slice(0, at) + text + draft.value.slice(el.selectionEnd ?? at)
}

async function onPaste(e) {
  if (!props.attach || !imageFilesFrom(e.clipboardData).length) return
  e.preventDefault()
  // One insert for the whole paste, not one per image: `insertAtCaret` reads
  // the caret off the live textarea, and Vue has not flushed the previous
  // write by the next iteration — so a second insert lands at the ORIGINAL
  // caret and a two-image paste comes out reversed.
  const refs = (await props.attach(e.clipboardData))
    .map(idx => `\n${imageRef(idx)}\n`).join('')
  if (refs) insertAtCaret(refs)
}
</script>

<template>
  <Card data-testid="feature-spec">
    <div class="fs-head">
      <div>
        <h2 class="card-header fs-title">Spec</h2>
        <p class="fs-sub">what this feature was declared to deliver</p>
      </div>
      <Button
        v-if="!editing && canWrite"
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

    <template v-if="editing">
      <Textarea
        ref="area"
        v-model="draft" :rows="14" class="fs-area" spellcheck="false"
        aria-label="Feature spec markdown" data-testid="feature-spec-input"
        @paste="onPaste"
      />
      <p class="fs-hint">Markdown. {{ IMAGE_REF_HINT }} Editing it appends no event.</p>
      <p v-if="error" class="fs-error" role="alert" data-testid="feature-spec-error">
        {{ error }}
      </p>
    </template>

    <MarkdownContent v-else-if="body" class="fs-body" :markdown="rendered" />
    <p v-else class="fs-empty" data-testid="feature-spec-empty">
      Declared with no spec. Without one there is nothing for the acceptance
      list to be checked against.
    </p>
  </Card>
</template>

<style scoped>
.fs-head {
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: 0.75rem;
}
.fs-title { margin-bottom: 0; }
.fs-sub { font-size: 11.5px; color: var(--color-fg-subtle); }
.fs-actions { display: flex; gap: 0.375rem; }
.fs-area {
    margin-top: 0.75rem;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.8125rem; line-height: 1.5;
}
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
