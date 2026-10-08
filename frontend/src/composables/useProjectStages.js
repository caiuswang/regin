import { computed, ref, toValue, watch } from 'vue'
import api from '../api'

// The variables a stage prompt is rendered with (lib/features/stages.py
// `_PROMPT_VARIABLES`). The stage view carries the body, not the palette.
export const STAGE_PROMPT_VARIABLES = [
  { name: 'feature_named', description: 'The feature as the prompt names it.', example: '#80 (Agent SDK runs)' },
  { name: 'spec_cmd', description: 'The literal `regin feature spec` write command for this feature.', example: '' },
  { name: 'stage_label', description: 'The stage this step belongs to.', example: 'Spec' },
]

export const STAGE_ROLES = [
  { value: 'spec', label: 'spec' },
  { value: 'build', label: 'build' },
]

// `null`/`undefined` is a feature with no project: it reads the global list.
export function stagesPath(project) {
  return `/projects/${project == null || project === '' ? 'default' : project}/stages`
}

export const liveStages = stages => (stages || [])
  .filter(s => !s.archived_at)
  .sort((a, b) => a.position - b.position)

/**
 * useProjectStages — one project's step stages, archived rows included.
 *
 * Every answer is checked against the project it was asked for, so a page that
 * moves to another project mid-read never shows the previous one's list.
 */
export function useProjectStages(project) {
  const stages = ref([])
  const inherited = ref(false)
  const loading = ref(true)
  const error = ref('')

  async function load() {
    const want = toValue(project)
    loading.value = true
    error.value = ''
    try {
      const body = await api.get(stagesPath(want))
      if (want !== toValue(project)) return
      stages.value = body.stages || []
      inherited.value = !!body.inherited
    } catch (e) {
      if (want === toValue(project)) error.value = e?.message || String(e)
    } finally {
      if (want === toValue(project)) loading.value = false
    }
  }

  watch(() => toValue(project), () => { stages.value = []; load() }, { immediate: true })

  const live = computed(() => liveStages(stages.value))
  return { stages, live, inherited, loading, error, load }
}
