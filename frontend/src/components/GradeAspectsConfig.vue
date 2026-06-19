<script setup>
import { onMounted, ref } from 'vue'
import api from '../api'
import Card from './Card.vue'
import Button from './ui/Button.vue'
import Checkbox from './ui/Checkbox.vue'
import Select from './ui/Select.vue'
import { useFlash } from '../composables/useFlash'

const emit = defineEmits(['config-loaded'])
const { flash } = useFlash()

const open = ref(false)
const loading = ref(true)
const saving = ref(false)
const aspects = ref([])
const providers = ref([])
const externalAgent = ref('')
const draft = ref({ label: '', description: '' })

function slugify(label) {
  return label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}

async function load() {
  loading.value = true
  try {
    const cfg = await api.get('/grader/config')
    aspects.value = cfg.aspects || []
    providers.value = cfg.providers || []
    externalAgent.value = cfg.external_agent || ''
    emit('config-loaded', { providers: providers.value, externalAgent: externalAgent.value, aspects: aspects.value })
  } catch {
    flash('failed to load grader settings', 'error')
  } finally {
    loading.value = false
  }
}

function addAspect() {
  const label = draft.value.label.trim()
  if (!label) return
  const key = slugify(label)
  if (!key || aspects.value.some(a => a.key === key)) {
    flash('aspect name is taken or invalid', 'error')
    return
  }
  aspects.value.push({
    key, label, description: draft.value.description.trim(),
    enabled: true, builtin: false,
  })
  draft.value = { label: '', description: '' }
}

function removeAspect(key) {
  aspects.value = aspects.value.filter(a => a.key !== key)
}

async function save() {
  saving.value = true
  try {
    const res = await api.put('/grader/config', {
      aspects: aspects.value,
      external_agent: externalAgent.value || null,
    })
    if (res && res.ok === false) {
      flash(res.error || 'save failed', 'error')
      return
    }
    aspects.value = res.aspects || aspects.value
    flash('grader settings saved')
    emit('config-loaded', { providers: providers.value, externalAgent: externalAgent.value, aspects: aspects.value })
  } catch {
    flash('save failed', 'error')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <Card class="mb-5" :no-padding="true">
    <button
      type="button"
      class="config-header focus-visible:outline-2 focus-visible:outline-blue-500"
      :aria-expanded="open"
      @click="open = !open"
    >
      <span class="config-title">Grader settings</span>
      <span class="config-sub">aspects the judge weighs · default judge provider</span>
      <span class="config-chevron">{{ open ? '▾' : '▸' }}</span>
    </button>

    <div v-if="open" class="config-body">
      <div v-if="loading" class="empty-state">Loading…</div>
      <template v-else>
        <div class="field">
          <label class="form-label" for="judge-provider">Default judge provider</label>
          <Select
            id="judge-provider"
            block
            v-model="externalAgent"
            :options="[{ value: '', label: 'first configured agent' }, ...providers.map(p => ({ value: p, label: p }))]"
          />
          <p v-if="!providers.length" class="field-hint">
            No judge agents configured — add one under
            <code>topic_proposal_external_agents</code> in settings.
          </p>
        </div>

        <div class="field">
          <span class="form-label">Evaluation aspects</span>
          <p class="field-hint">
            Enabled aspects are woven into the deep judge's prompt. Built-in
            aspects mirror the grounded axes and can be toggled but not removed.
          </p>
          <div v-for="a in aspects" :key="a.key" class="aspect-row">
            <Checkbox v-model="a.enabled" class="aspect-toggle">
              <span class="aspect-label-wrap">
                <span class="aspect-label">{{ a.label }}</span>
                <span v-if="a.builtin" class="badge badge-purple">built-in</span>
              </span>
            </Checkbox>
            <input
              v-model="a.description"
              class="input aspect-desc focus-visible:outline-2 focus-visible:outline-blue-500"
              placeholder="what the judge should check"
            >
            <Button
              variant="secondary"
              size="sm"
              :disabled="a.builtin"
              :title="a.builtin ? 'Built-in aspects cannot be removed' : 'Remove aspect'"
              @click="removeAspect(a.key)"
            >
              Remove
            </Button>
          </div>
        </div>

        <div class="field add-aspect">
          <input
            v-model="draft.label"
            class="input focus-visible:outline-2 focus-visible:outline-blue-500"
            placeholder="new aspect name (e.g. Test coverage)"
            @keyup.enter="addAspect"
          >
          <input
            v-model="draft.description"
            class="input aspect-desc focus-visible:outline-2 focus-visible:outline-blue-500"
            placeholder="what it checks"
            @keyup.enter="addAspect"
          >
          <Button
            variant="secondary"
            size="sm"
            @click="addAspect"
          >
            Add
          </Button>
        </div>

        <div class="config-actions">
          <Button
            variant="primary"
            :disabled="saving"
            @click="save"
          >
            {{ saving ? 'Saving…' : 'Save grader settings' }}
          </Button>
        </div>
      </template>
    </div>
  </Card>
</template>

<style scoped>
.config-header {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.85rem 1rem;
  background: none;
  border: none;
  cursor: pointer;
  text-align: left;
}
.config-title { font-weight: 600; font-size: 0.95rem; }
.config-sub { font-size: 0.78rem; color: var(--color-slate-500); }
.config-chevron { margin-left: auto; color: var(--color-slate-400); }
.config-body { padding: 0 1rem 1rem; border-top: 1px solid var(--color-slate-200); }
.field { margin-top: 1rem; }
.form-label {
  display: block;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-slate-500);
  margin-bottom: 0.35rem;
}
.field-hint { font-size: 0.75rem; color: var(--color-slate-500); margin: 0.25rem 0 0.5rem; }
.aspect-row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.4rem 0;
}
.aspect-toggle {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  min-width: 12rem;
  flex-shrink: 0;
}
.aspect-label-wrap { display: inline-flex; align-items: center; gap: 0.45rem; }
.aspect-label { font-size: 0.85rem; font-weight: 500; text-transform: capitalize; }
.aspect-desc { flex: 1; }
.add-aspect { display: flex; gap: 0.5rem; align-items: center; }
.config-actions { margin-top: 1rem; display: flex; justify-content: flex-end; }
</style>
