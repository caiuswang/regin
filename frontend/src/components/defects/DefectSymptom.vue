<script setup>
// The symptom, edited where it is read: a click turns it into the field, so
// correcting the wording is not a menu verb with a form of its own.
import { computed, nextTick, ref } from 'vue'
import api from '../../api'
import { refusalOf, runRemedy } from '../../composables/useDefectRemedy'
import { useConfirm } from '../../composables/useConfirm'
import { imageFilesFrom, toBase64 } from '../../utils/imageFiles'
import Button from '../ui/Button.vue'
import RefInlineText from '../refs/RefInlineText.vue'
import RefTextarea from '../refs/RefTextarea.vue'
import DefectRefusal from './DefectRefusal.vue'

const props = defineProps({
  defect: { type: Object, required: true },
  canWrite: { type: Boolean, default: false },
  clamped: { type: Boolean, default: false },
})
const emit = defineEmits(['changed'])

const { confirm } = useConfirm()
const editing = ref(false)
const value = ref('')
const busy = ref(false)
const refusal = ref(null)
const field = ref(null)

const editable = computed(() => props.canWrite && !props.clamped)
const blank = computed(() => !value.value.trim())

async function start(e) {
  // A reference inside the wording is a link, not a request to edit.
  if (!editable.value || e?.target?.closest?.('a')) return
  value.value = props.defect.symptom
  refusal.value = null
  editing.value = true
  await nextTick()
  field.value?.$el?.querySelector?.('textarea')?.focus()
}

function cancel() {
  editing.value = false
  refusal.value = null
}

async function save() {
  if (blank.value || busy.value) return
  const symptom = value.value.trim()
  if (symptom === props.defect.symptom) return cancel()
  busy.value = true
  refusal.value = null
  try {
    const body = await api.patch(`/defects/${props.defect.id}`, { symptom })
    if (body?.ok === false) {
      refusal.value = refusalOf(body)
      return
    }
    editing.value = false
    emit('changed', `#${props.defect.id} edited.`)
  } catch (e) {
    refusal.value = refusalOf(e?.message || String(e))
  } finally {
    busy.value = false
  }
}

// A screenshot pasted while correcting the wording is evidence for this
// defect, written at once; a text paste is left to the field.
async function onPaste(e) {
  const files = imageFilesFrom(e.clipboardData)
  if (!files.length) return
  e.preventDefault()
  busy.value = true
  try {
    for (const file of files) {
      const data_b64 = await toBase64(file)
      const body = await api.post(`/defects/${props.defect.id}/images`, { data_b64 })
      if (body?.ok === false) {
        refusal.value = refusalOf(body)
        return
      }
    }
    emit('changed', `#${props.defect.id}: ${files.length} image${files.length === 1 ? '' : 's'} attached.`)
  } finally {
    busy.value = false
  }
}

async function onRemedy(remedy) {
  busy.value = true
  const result = await runRemedy(remedy, { confirm }).finally(() => { busy.value = false })
  if (!result.ok) {
    if (result.refusal) refusal.value = result.refusal
    return
  }
  editing.value = false
  refusal.value = null
  emit('changed', result.message, result.keeper ? { keeper: result.keeper } : undefined)
}
</script>

<template>
  <form v-if="editing" class="ds-edit" @submit.prevent="save">
    <label class="sr-only" :for="`ds-${defect.id}`">Symptom of defect #{{ defect.id }}</label>
    <RefTextarea
      :id="`ds-${defect.id}`"
      ref="field"
      v-model="value"
      rows="3"
      :feature="defect.feature_id"
      :disabled="busy"
      :error="blank"
      :data-testid="`defect-edit-field-${defect.id}`"
      @keydown.esc.stop.prevent="cancel"
      @keydown.meta.enter.prevent="save"
      @keydown.ctrl.enter.prevent="save"
      @paste="onPaste"
    />
    <div class="ds-actions">
      <Button type="submit" size="sm" :disabled="busy || blank"
              :data-testid="`defect-edit-submit-${defect.id}`">Save</Button>
      <Button variant="ghost" size="sm" :disabled="busy"
              :data-testid="`defect-edit-cancel-${defect.id}`"
              @click="cancel">Cancel</Button>
      <span class="ds-hint">⌘↵ to save · Esc to cancel · paste a screenshot to attach it</span>
    </div>
    <DefectRefusal v-if="refusal" :refusal="refusal" :busy="busy"
                   :testid="`defect-edit-refusal-${defect.id}`" @remedy="onRemedy" />
  </form>
  <p
    v-else-if="editable"
    class="symptom ds-editable cursor-pointer hover:bg-[var(--color-surface-2)] focus-visible:outline-2 focus-visible:outline-blue-500"
    data-pl-part="symptom"
    role="button" tabindex="0" title="Click to edit"
    :aria-label="`Edit the symptom of defect #${defect.id}`"
    :data-testid="`defect-symptom-${defect.id}`"
    @click="start"
    @keydown.enter.self.prevent="start"
  ><RefInlineText :text="defect.symptom" /></p>
  <p
    v-else
    class="symptom" :class="{ 'symptom-clamped': clamped }" data-pl-part="symptom"
    :title="clamped ? defect.symptom : undefined"
    :data-testid="`defect-symptom-${defect.id}`"
  ><RefInlineText :text="defect.symptom" /></p>
</template>

<style scoped>
.symptom {
    max-width: 60rem; font-size: 13px; line-height: 1.5;
    color: var(--color-fg); white-space: pre-wrap; overflow-wrap: anywhere;
}
.symptom-clamped {
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ds-editable {
    border-radius: var(--radius-md);
    margin: -0.15rem -0.3rem; padding: 0.15rem 0.3rem;
}
.ds-edit { display: flex; flex-direction: column; gap: 0.4rem; max-width: 60rem; }
.ds-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem; }
.ds-hint { font-size: 11px; color: var(--color-fg-faint); }
</style>
