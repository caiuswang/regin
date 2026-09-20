<script setup>
// Capture an idea without leaving the page you are on.
//
// The /tasks composer is a destination: reaching it costs a navigation, and a
// navigation mid-thought is the thing that loses the idea. This is the same
// capture bound to a bubble and a chord, mounted once by the app shell, so it
// is reachable from every route and returns you to exactly where you were.
//
// It is deliberately narrower than the /tasks composer — title, details,
// images, and the issue field. Tags are curation, and curation is what /tasks
// is for; the issue field is the opposite of curation, since pasting a
// reference is what lets an idea be captured without typing anything at all.
import { ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Dialog from '../ui/Dialog.vue'
import Input from '../ui/Input.vue'
import NavIcon from '../NavIcon.vue'
import TaskImageStrip from './TaskImageStrip.vue'
import Textarea from '../ui/Textarea.vue'
import { imageFilesFrom } from '../../utils/imageFiles'
import { TITLE_MAX } from '../../composables/useTasks.js'
import { useFlash } from '../../composables/useFlash.js'
import { useQuickCapture } from '../../composables/useQuickCapture.js'
import { useAttachedImages } from '../../composables/useAttachedImages.js'
import { caretTarget, withCaretInsert } from '../../utils/caret'
import { imageRefBlock } from '../../utils/taskBodyImages'

defineProps({
  // The phone blocker bar is `position: fixed` at the bottom of the viewport;
  // the bubble has to step over it rather than sit under it.
  inset: { type: Boolean, default: false },
  // True only for `meta.fixedViewport === true` — today just /live, whose
  // composer runs the full width of a phone exactly where this bubble sits,
  // covering its meta row and swallowing taps meant for it on a view that
  // cannot be scrolled to reveal what is underneath. There is no free corner
  // at that width, so the bubble stands down and the drawer's "New idea"
  // entry carries capture instead. A `'lg'` route (/inbox, the trace view)
  // scrolls normally on a phone and is NOT crowded.
  crowded: { type: Boolean, default: false },
})

const { flash } = useFlash()
const { open, draft, error, create, captured } = useQuickCapture()
const {
  items: images, busy: imagesBusy, error: imagesError,
  add: addImages, remove: removeImage, flush: flushImages, reset: resetImages,
} = useAttachedImages()

const titleInput = ref(null)

// Spans the POST *and* the image flush, and is the single condition behind
// both the Save button and the Enter key — a keyboard path guarded less
// tightly than the button it mirrors is how a held Enter captures the same
// idea twice.
const submitting = ref(false)

// The text survives a close (that is what makes Escape safe), but the images
// cannot: they are held as browser blobs against a task id that does not exist
// yet, and a thumbnail still sitting there on the next open would belong to an
// idea that was never captured.
watch(open, (isOpen) => { if (!isOpen) resetImages() })

function focusTitle() {
  titleInput.value?.focus()
}

const bodyArea = ref(null)

// Attaching is only half of it: the bytes are useless to a reader until the
// prose names them, and `![](image:N)` is unguessable. The index a held file
// carries is allocated in the browser and asked for at flush, so the reference
// is writable at the moment of the paste rather than after the row exists.
async function takeImages(source, event) {
  const refs = imageRefBlock(await addImages(source))
  if (!refs) return
  draft.value.body = withCaretInsert(
    caretTarget(event, bodyArea.value), draft.value.body, refs)
}

async function onPaste(e) {
  if (!imageFilesFrom(e.clipboardData).length) return
  e.preventDefault()
  await takeImages(e.clipboardData, e)
}

async function onDrop(e) {
  if (!imageFilesFrom(e.dataTransfer).length) return
  e.preventDefault()
  await takeImages(e.dataTransfer, e)
}

async function submit() {
  if (submitting.value) return
  submitting.value = true
  try {
    const task = await create()
    if (!task) return
    await flushImages(task.id)
    resetImages()
    captured(task)
    flash('Idea captured')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <Button
    variant="primary"
    size="icon"
    class="quick-capture-fab h-12 w-12 rounded-full"
    :class="{ 'is-inset': inset, 'is-crowded': crowded }"
    aria-label="Capture an idea"
    title="Capture an idea (⌘K, then ⌃I)"
    data-testid="quick-capture-fab"
    @click="open = true"
  >
    <NavIcon name="idea" :size="20" />
  </Button>

  <Dialog
    v-model:open="open"
    title="Capture an idea"
    description="Lands in Tasks under Ideas. Nothing runs until someone arms it."
    @open-auto-focus="focusTitle"
  >
    <form
      class="quick-capture-form"
      data-testid="quick-capture-form"
      @submit.prevent="submit"
      @paste="onPaste"
      @dragover.prevent
      @drop="onDrop"
    >
      <div>
        <label class="field-label" for="quick-capture-title">Title</label>
        <Input
          id="quick-capture-title"
          ref="titleInput"
          v-model="draft.title"
          type="text"
          :error="!!error"
          :placeholder="`What should happen? (${TITLE_MAX} characters max)`"
          aria-label="Idea title"
          @keyup.enter="submit"
        />
      </div>
      <div>
        <label class="field-label" for="quick-capture-body">
          Details <span class="font-normal text-slate-400">(optional)</span>
        </label>
        <Textarea
          id="quick-capture-body"
          ref="bodyArea"
          v-model="draft.body"
          :rows="3"
          aria-label="Idea details"
          @keydown.enter.meta.prevent="submit"
          @keydown.enter.ctrl.prevent="submit"
        />
      </div>
      <div>
        <span class="field-label">Images <span class="font-normal text-slate-400">(paste or drop)</span></span>
        <TaskImageStrip
          :items="images"
          :busy="imagesBusy"
          :error="imagesError"
          @pick="takeImages"
          @remove="removeImage"
        />
      </div>
    </form>

    <p v-if="error" class="quick-capture-error" role="alert" data-testid="quick-capture-error">
      {{ error }}
    </p>

    <template #footer>
      <span class="quick-capture-hint">Esc keeps the draft · ⌘↵ saves</span>
      <Button variant="ghost" @click="open = false">Cancel</Button>
      <Button
        variant="primary"
        :disabled="submitting || !draft.title.trim()"
        data-testid="quick-capture-save"
        @click="submit"
      >
        {{ submitting ? 'Capturing…' : 'Capture idea' }}
      </Button>
    </template>
  </Dialog>
</template>

<style scoped>
/* Sizing and colour come from the Button primitive; only the placement and
   the lift are this component's business. */
.quick-capture-fab {
    position: fixed;
    /* `--corner-fab-inset` is the shared reservation (style.css); `--trace-rail-h`
       and `--trace-dock-h` are bands the trace session rail and the follow-up
       composer publish from their own measured heights. The rail is full-bleed
       at every width, so that clearance is not a phone concern — unoffset, this
       bubble sat on top of the rail at 1440 exactly as it did at 375. Measured
       tokens, never guessed constants, are what keep the corner's occupants
       from re-colliding when one of them grows a row. */
    right: var(--corner-fab-inset);
    bottom: calc(var(--corner-fab-inset) + max(var(--trace-rail-h, 0px), var(--trace-dock-h, 0px), var(--blocker-bar-h, 0px)));
    /* Over the page, under every dialog — including its own. */
    z-index: var(--z-dropdown);
    box-shadow: var(--ds-shadow-lg);
    transition: transform 0.15s ease, box-shadow 0.15s ease;
}

.quick-capture-fab:hover { transform: translateY(-1px); }

@media (max-width: 767px) {
  .quick-capture-fab {
      bottom: calc(
        var(--corner-fab-inset)
        + max(var(--trace-rail-h, 0px), var(--trace-dock-h, 0px), var(--blocker-bar-h, 0px))
        + env(safe-area-inset-bottom)
      );
  }
  /* The phone blocker bar is 3.875rem tall (AppLayout uses the same figure
     for the scroller's bottom inset); the extra 1rem is this bubble's own
     gap above it. */
  .quick-capture-fab.is-inset {
      bottom: calc(4.875rem + env(safe-area-inset-bottom));
  }
  /* Breakpoint, not `v-if`: the suppression is purely about there being no
     free corner at this width. */
  .quick-capture-fab.is-crowded { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  .quick-capture-fab { transition: none; }
  .quick-capture-fab:hover { transform: none; }
}

.quick-capture-form {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    min-width: 0;
}

.quick-capture-error {
    margin-top: 0.75rem;
    font-size: 0.8125rem;
    color: var(--color-danger);
}
</style>

<style>
/* The footer is portalled with the dialog, so the hint cannot be reached by a
   scoped rule; it is pushed left of the buttons the footer right-aligns. */
.quick-capture-hint {
    margin-right: auto;
    align-self: center;
    font-size: 0.6875rem;
    color: var(--color-fg-subtle);
}
</style>
