<script setup>
import { onMounted, ref } from 'vue'
import api from '../api'
import Card from './Card.vue'
import Button from './ui/Button.vue'
import { useFlash } from '../composables/useFlash'

const { flash } = useFlash()
const loading = ref(true)
const saving = ref(false)
const axes = ref([])
const showDefault = ref({})

function titleCase(key) {
  return key.charAt(0).toUpperCase() + key.slice(1)
}

async function load() {
  loading.value = true
  try {
    const cfg = await api.get('/grader/config')
    axes.value = Object.entries(cfg.system_prompts || {}).map(([key, v]) => ({
      key,
      override: v.override || '',
      default: v.default || '',
    }))
  } catch {
    flash('failed to load grader prompts', 'error')
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    const overrides = {}
    for (const a of axes.value) overrides[a.key] = a.override
    const res = await api.put('/grader/config', { system_prompt_overrides: overrides })
    if (res && res.ok === false) {
      flash(res.error || 'save failed', 'error')
      return
    }
    flash('grader prompts saved')
    await load()
  } catch {
    flash('save failed', 'error')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div v-if="loading" class="empty-state">Loading grader prompts…</div>
  <div v-else>
    <p class="panel-intro">
      The deep (LLM-judge) tier runs these system prompts. Leave a box empty to
      use the built-in default; type to override it for this deployment. Enabled
      aspects are appended automatically — edit those under <em>Grades →
      Grader settings</em>.
    </p>

    <Card v-for="axis in axes" :key="axis.key" class="mb-4">
      <div class="prompt-head">
        <h3 class="prompt-title">{{ titleCase(axis.key) }} judge</h3>
        <div class="prompt-head-actions">
          <span v-if="!axis.override.trim()" class="badge badge-gray">using default</span>
          <span v-else class="badge badge-blue">overridden</span>
          <Button
            variant="secondary"
            size="sm"
            @click="showDefault[axis.key] = !showDefault[axis.key]"
          >
            {{ showDefault[axis.key] ? 'Hide' : 'View' }} default
          </Button>
          <Button
            variant="secondary"
            size="sm"
            :disabled="!axis.override.trim()"
            @click="axis.override = ''"
          >
            Reset
          </Button>
        </div>
      </div>
      <textarea
        v-model="axis.override"
        rows="10"
        class="topics-input w-full font-mono text-xs focus-visible:outline-2 focus-visible:outline-blue-500"
        :placeholder="`Using built-in default — type to override the ${axis.key} judge prompt`"
      ></textarea>
      <pre v-if="showDefault[axis.key]" class="default-preview">{{ axis.default }}</pre>
    </Card>

    <div class="flex justify-end">
      <Button variant="primary" :disabled="saving" @click="save">
        {{ saving ? 'Saving…' : 'Save grader prompts' }}
      </Button>
    </div>
  </div>
</template>

<style scoped>
.panel-intro {
  font-size: 0.85rem;
  color: var(--color-slate-600);
  margin-bottom: 1rem;
}
.prompt-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.5rem;
}
.prompt-title {
  font-size: 0.95rem;
  font-weight: 600;
}
.prompt-head-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.default-preview {
  margin-top: 0.5rem;
  padding: 0.75rem;
  background: var(--code-bg);
  color: var(--code-fg);
  border-radius: 6px;
  font-size: 0.72rem;
  line-height: 1.45;
  white-space: pre-wrap;
  max-height: 18rem;
  overflow-y: auto;
}
</style>
