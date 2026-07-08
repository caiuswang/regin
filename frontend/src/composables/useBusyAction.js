import { ref } from 'vue'

// Single in-flight-action tracker shared by detail views that disable their
// button rows while one mutation runs. `isBusy()` → any action in flight;
// `isBusy('x')` → that specific action.
export function useBusyAction() {
  const busyAction = ref('')
  function startBusy(action) { busyAction.value = action }
  function stopBusy() { busyAction.value = '' }
  function isBusy(action = '') {
    if (!busyAction.value) return false
    return action ? busyAction.value === action : true
  }
  return { busyAction, startBusy, stopBusy, isBusy }
}
