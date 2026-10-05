<script setup>
// The viewer toolbar (R3). It changes nothing itself: a view, layout or
// anchor change is emitted for the mount to write (the /files URL, or the
// drawer's store), and a different file is a `navigate`, like a relative link.
import { computed, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import { useCopy } from '../../composables/useCopy'
import {
  TEXT_BACKED, downloadFile, isLoopbackHost, lineAnchor, vscodeHref,
} from './actions'
import { filesPageHref, rawUrl } from './source'

const props = defineProps({
  meta: { type: Object, required: true },
  source: { type: Object, required: true },
  view: { type: String, required: true },
  layout: { type: String, default: '' },
  hasRendered: { type: Boolean, default: false },
  lines: { type: Object, default: null },
})
const emit = defineEmits(['view', 'layout', 'anchor', 'navigate'])

const { copyText, isCopied } = useCopy()
const error = ref('')

const isPathSource = computed(() => !!props.source.path)
const atHead = computed(() => props.source.rev === 'HEAD')
const atWorktree = computed(() => !props.source.rev || props.source.rev === 'WORKTREE')
const showRevSwitch = computed(() => isPathSource.value && (props.meta.in_head || atHead.value))
const copyPath = computed(() => (props.source.project ? props.source.path : props.meta.abs_path) || '')
const canOnDisk = computed(() => !!props.source.span && !!props.meta.abs_path)
const vscode = computed(() => (props.meta.abs_path && isLoopbackHost()
  ? vscodeHref(props.meta.abs_path, props.lines?.start)
  : ''))

function setRev(rev) {
  if (rev === 'HEAD' ? atHead.value : atWorktree.value) return
  const next = { ...props.source }
  if (rev === 'HEAD') next.rev = 'HEAD'
  else delete next.rev
  emit('navigate', next, '')
}

function toggleDiff() {
  if (props.view !== 'diff') emit('view', 'diff')
  else emit('view', props.hasRendered ? 'source' : '')
}

function openOnDisk() {
  emit('navigate', { session: props.source.session, path: props.meta.abs_path }, '')
}

async function run(action) {
  error.value = ''
  try {
    await action()
  } catch (err) {
    error.value = err.message || 'The action failed.'
  }
}

const copyContent = () => run(async () => copyText(await api.getText(rawUrl(props.source)), 'content'))
const download = () => run(() => downloadFile(props.source, props.meta.name))

// The link names the page, not the drawer, so it opens the same view in any
// tab; on /files it also becomes the current URL's anchor.
function copyLink() {
  const anchor = lineAnchor(props.lines)
  const href = filesPageHref(props.source, { view: props.view, layout: props.layout }, anchor)
  emit('anchor', anchor)
  copyText(`${window.location.origin}${href}`, 'link')
}

const pressed = (on) => (on ? 'true' : 'false')
const variant = (on) => (on ? 'primary' : 'secondary')
</script>

<template>
  <div class="fa" role="toolbar" aria-label="File actions" data-testid="file-actions">
    <div v-if="showRevSwitch" class="fa-group" role="group" aria-label="Revision">
      <Button size="sm" :variant="variant(atWorktree)" :aria-pressed="pressed(atWorktree)"
              data-testid="file-rev-worktree" @click="setRev('WORKTREE')">Worktree</Button>
      <Button size="sm" :variant="variant(atHead)" :aria-pressed="pressed(atHead)"
              data-testid="file-rev-head" @click="setRev('HEAD')">HEAD</Button>
    </div>
    <div v-if="hasRendered && view !== 'diff'" class="fa-group" role="group" aria-label="View mode">
      <Button size="sm" :variant="variant(view !== 'source')" :aria-pressed="pressed(view !== 'source')"
              data-testid="file-view-rendered" @click="emit('view', '')">Rendered</Button>
      <Button size="sm" :variant="variant(view === 'source')" :aria-pressed="pressed(view === 'source')"
              data-testid="file-view-source" @click="emit('view', 'source')">Source</Button>
    </div>
    <Button v-if="meta.diffable" size="sm" :variant="variant(view === 'diff')"
            :aria-pressed="pressed(view === 'diff')" data-testid="file-action-diff"
            @click="toggleDiff">Changes vs HEAD</Button>
    <div v-if="view === 'diff'" class="fa-group" role="group" aria-label="Diff layout">
      <Button size="sm" :variant="variant(layout !== 'inline')" :aria-pressed="pressed(layout !== 'inline')"
              data-testid="file-diff-side" @click="emit('layout', '')">Side by side</Button>
      <Button size="sm" :variant="variant(layout === 'inline')" :aria-pressed="pressed(layout === 'inline')"
              data-testid="file-diff-inline" @click="emit('layout', 'inline')">Inline</Button>
    </div>
    <Button v-if="canOnDisk" size="sm" data-testid="file-action-on-disk" @click="openOnDisk">
      Current on disk
    </Button>
    <span class="fa-sep" aria-hidden="true" />
    <Button v-if="copyPath" size="sm" variant="ghost" data-testid="file-action-copy-path"
            @click="copyText(copyPath, 'path')">{{ isCopied('path') ? 'Copied' : 'Copy path' }}</Button>
    <Button v-if="TEXT_BACKED.has(meta.preview)" size="sm" variant="ghost" data-testid="file-action-copy-content"
            @click="copyContent">{{ isCopied('content') ? 'Copied' : 'Copy content' }}</Button>
    <Button size="sm" variant="ghost" data-testid="file-action-copy-link"
            @click="copyLink">{{ isCopied('link') ? 'Copied' : 'Copy link' }}</Button>
    <Button size="sm" variant="ghost" data-testid="file-action-download" @click="download">Download</Button>
    <Button v-if="vscode" as="a" :href="vscode" size="sm" variant="ghost"
            data-testid="file-action-vscode">Open in VS Code</Button>
    <p v-if="error" class="fa-error" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.fa {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.35rem;
  min-width: 0;
}
.fa-group { display: flex; gap: 0.25rem; }
.fa-sep { flex: 1; }
.fa-error { flex-basis: 100%; font-size: 12px; color: var(--color-danger); }
</style>
