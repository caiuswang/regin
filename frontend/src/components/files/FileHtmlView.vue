<script setup>
// Rendered HTML runs only in a sandboxed srcdoc frame (D4). Scripts are off
// until the operator asks; turning them on adds `allow-scripts` and nothing
// else. Without same-origin access the frame's origin is opaque, so even a
// running script cannot reach regin's DOM, storage or token. The viewer
// remounts this view on every source change, which resets the toggle.
import { onMounted, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import { rawUrl } from './source'

const props = defineProps({
  meta: { type: Object, required: true },
  source: { type: Object, required: true },
  lines: { type: Object, default: null },
})
const emit = defineEmits(['failed'])

const text = ref(null)
const error = ref('')
const scripts = ref(false)

onMounted(async () => {
  try {
    text.value = await api.getText(rawUrl(props.source))
  } catch (err) {
    error.value = err.message || 'The file could not be loaded.'
    emit('failed', error.value)
  }
})
</script>

<template>
  <div class="fhv" data-testid="file-html-view" :data-scripts="scripts ? 'on' : 'off'">
    <p v-if="error" class="fhv-note fhv-error" role="alert">{{ error }}</p>
    <p v-else-if="text == null" class="fhv-note" role="status">Loading…</p>
    <template v-else>
      <div class="fhv-bar">
        <span v-if="scripts" class="fhv-warn">Scripts are running in an isolated frame.</span>
        <Button size="sm" :variant="scripts ? 'primary' : 'secondary'" data-testid="file-html-scripts"
                :aria-pressed="scripts ? 'true' : 'false'" @click="scripts = !scripts">
          Run scripts
        </Button>
      </div>
      <!-- A sandbox change applies only on the frame's next navigation; the key reloads it. -->
      <iframe :key="scripts ? 'on' : 'off'" :sandbox="scripts ? 'allow-scripts' : ''" :srcdoc="text"
              :title="meta.name" referrerpolicy="no-referrer" class="fhv-frame"
              data-testid="file-html-frame" />
    </template>
  </div>
</template>

<style scoped>
.fhv {
  flex: 1;
  min-height: 20rem;
  display: flex;
  flex-direction: column;
}
.fhv-note { padding: 1rem 1.25rem; font-size: 13px; color: var(--color-fg-muted); }
.fhv-error { color: var(--color-danger); }
.fhv-bar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 0.6rem;
  padding: 0.4rem 0.85rem;
  border-bottom: 1px solid var(--color-border);
}
.fhv-warn { font-size: 12px; color: var(--color-fg-muted); }
.fhv-frame { flex: 1; min-height: 0; width: 100%; border: 0; background: #fff; }
</style>
