<script setup>
import Badge from '../Badge.vue'

defineProps({
  refs: { type: Array, default: () => [] },
  title: { type: String, default: 'References' },
})

function roleColor(role) {
  if (role === 'test') return 'green'
  if (role === 'architecture' || role === 'overview') return 'blue'
  if (role === 'api' || role === 'entrypoint') return 'purple'
  return 'gray'
}
</script>

<template>
  <div>
    <h3 class="topics-subsection-title">{{ title }}</h3>
    <div class="overflow-x-auto">
      <table class="tbl">
        <thead><tr><th>Role</th><th>Tier</th><th>Path</th></tr></thead>
        <tbody>
          <tr v-for="ref in (refs || [])" :key="`${ref.role}:${ref.path}`">
            <td><Badge :color="roleColor(ref.role)" :label="ref.role" /></td>
            <td><Badge :color="(ref.tier || 'primary') === 'primary' ? 'blue' : 'gray'" :label="ref.tier || 'primary'" /></td>
            <td><code class="text-xs">{{ ref.path }}</code></td>
          </tr>
          <tr v-if="!(refs || []).length">
            <td colspan="3" class="text-gray-500">No references recorded.</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
