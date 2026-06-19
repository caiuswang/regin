import { computed } from 'vue'

// Provider skill-path metadata derived from a /skills or /skills/<id> API
// payload. Shared by SkillsView and SkillDetailView so the default-path
// fallbacks and the deployment badge label live in one place instead of
// being copy-pasted (and drifting) across the two views.
export function useProviderPaths(data) {
  const provider = computed(() => data.value?.provider || {})
  const enabledProviders = computed(() => data.value?.enabled_providers || [provider.value])
  const globalDir = computed(() => provider.value.global_dir || '~/.claude/skills')
  const projectSubpath = computed(() => provider.value.project_subpath || '.claude/skills')
  const providerName = computed(() => provider.value.name || 'Agent')

  function providerBadge(providerId) {
    const p = enabledProviders.value.find(x => x.id === providerId)
      || { name: providerId || 'active' }
    return p.name
  }

  return { provider, enabledProviders, globalDir, projectSubpath, providerName, providerBadge }
}
