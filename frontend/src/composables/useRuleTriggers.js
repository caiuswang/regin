import { ref, computed, watch } from 'vue'
import api from '../api'

// Trigger rows for the currently-selected `rule.check` span, keyed by
// rule_id. Lets the applicable_rules list bind a 🔇 button to each row (the
// span attributes don't carry trigger ids). Refetched whenever the selected
// span changes and after every suppress/unsuppress (call `reload`).
//
// `selectedSpan` is the shared ref owned by the SFC. `canSuppressRule` gates
// the suppress UI by the stored user role.
export function useRuleTriggers(selectedSpan) {
  const ruleTriggersByRuleId = ref({})

  const currentUser = api.getStoredUser ? api.getStoredUser() : null
  const canSuppressRule = computed(() => {
    const role = currentUser?.role
    return role === 'admin' || role === 'editor'
  })

  async function loadTriggersForSelectedSpan() {
    if (!selectedSpan.value || selectedSpan.value.name !== 'rule.check') {
      ruleTriggersByRuleId.value = {}
      return
    }
    const spanId = selectedSpan.value.span_id
    try {
      const data = await api.get(`/triggers/by-span/${encodeURIComponent(spanId)}`)
      const map = {}
      for (const t of data?.triggers || []) map[t.rule_id] = t
      ruleTriggersByRuleId.value = map
    } catch {
      ruleTriggersByRuleId.value = {}
    }
  }

  // When the user lands on (or away from) a rule.check span, refresh the
  // trigger map so the applicable_rules list can bind 🔇 buttons.
  watch(() => selectedSpan.value?.span_id, loadTriggersForSelectedSpan)

  return { ruleTriggersByRuleId, canSuppressRule, loadTriggersForSelectedSpan }
}
