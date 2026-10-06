<script setup>
// What a shell command changed, at reader scale — one DiffBlock per file.
//
// Its own component for the same reason the feed's BashEditDiffs is: inline,
// the loop's heads and the two overflow lists push PresentContent's template
// past the vue-complexity node and binding budgets.
import DiffBlock from '../DiffBlock.vue'
import FilePathLink from '../files/FilePathLink.vue'

defineProps({
  // presentFor().bashEdits
  edits: { type: Object, required: true },
})
</script>

<template>
  <div class="present-bash-edits">
    <div class="present-block-label">File changes</div>
    <div
      v-for="f in edits.files"
      :key="f.key"
      class="present-diff code-surface"
      data-present-bash-edit
    >
      <div class="present-diff-head">
        <FilePathLink :trace-id="edits.traceId" :path="f.filePath" class="present-diff-file" />
        <span v-if="f.op" class="present-diff-op">{{ f.op }}</span>
        <span v-if="f.added" class="present-diff-add">+{{ f.added }}</span>
        <span v-if="f.removed" class="present-diff-del">−{{ f.removed }}</span>
        <span v-if="f.droppedBytes" class="present-diff-dropped">
          {{ f.droppedBytes }} B dropped at capture
        </span>
      </div>
      <DiffBlock v-if="f.diff" numbered flush :diff="f.diff" :file-path="f.filePath" />
      <p v-else class="present-bash-note">no diff supplied</p>
    </div>
    <p v-if="edits.unavailable" class="present-bash-note">diff unavailable</p>
    <ul v-if="edits.extraPaths.length" class="present-bash-paths">
      <li v-for="p in edits.extraPaths" :key="p" data-present-bash-path>
        <FilePathLink :trace-id="edits.traceId" :path="p" class="present-bash-path" />
      </li>
    </ul>
    <p v-else-if="edits.more" class="present-bash-note">+{{ edits.more }} more files</p>
  </div>
</template>

<style scoped>
/* The heads, the diff wells and the block label are PresentContent's, and a
   scoped rule there does not cross into this subtree — so the shapes this
   block shares with the single-file diff are restated, not re-derived. */
.present-bash-edits { margin-top: 22px; }
.present-block-label {
  font-size: 10px; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--color-fg-subtle); margin-bottom: 10px;
}
.present-diff + .present-diff { margin-top: 18px; }
.present-diff-head {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  font-family: var(--font-mono); font-size: 11.5px; margin-bottom: 6px;
}
.present-diff-file, .present-bash-path { color: var(--color-fg-muted); word-break: break-all; }
.present-diff-file:hover, .present-bash-path:hover { color: var(--color-fg); }
.present-diff-op {
  color: var(--color-fg-subtle); text-transform: uppercase;
  letter-spacing: 0.08em; font-size: 10px;
}
.present-diff-add { color: var(--color-emerald-600); }
.present-diff-del { color: var(--color-red-600); }
.present-diff-dropped { color: var(--color-fg-faint); }
.present-diff :deep(.diff-block) {
  background: var(--code-bg); border-radius: 11px; margin: 0;
}
.present-bash-note {
  margin: 6px 0 0; font-family: var(--font-mono); font-size: 12px;
  color: var(--color-fg-faint);
}
.present-bash-paths {
  margin: 8px 0 0; padding: 0; list-style: none;
  font-family: var(--font-mono); font-size: 12px; color: var(--color-fg-muted);
}
.present-bash-paths li { word-break: break-all; line-height: 1.6; }
</style>
