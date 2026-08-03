import { ref } from 'vue'
import { notificationTier } from '../constants/inboxTypes'
import { useNotificationPrefs } from './useNotificationPrefs'

// The OS layer is deliberately quieter than the in-app one. An in-app toast is
// free — you are already looking at the tab. An OS banner interrupts whatever
// else you are doing, so it is spent only where the in-app surface cannot
// reach: a hidden tab, or a blocker (which is worth interrupting for even
// while the tab is in front, because the agent is stopped until you answer).
const SUPPORTED = typeof window !== 'undefined' && 'Notification' in window

const permission = ref(SUPPORTED ? Notification.permission : 'unsupported')
const lastPost = ref('')

export function useOsNotifications() {
  const { prefs, set } = useNotificationPrefs()

  const ready = () => SUPPORTED && permission.value === 'granted' && prefs.osEnabled

  function shouldPost(tier) {
    if (!ready()) return false
    if (tier === 3) return false             // count-only never reaches the OS
    return tier === 1 || document.hidden     // tab in front: the toast is enough
  }

  function post(message, { onOpen } = {}) {
    const tier = notificationTier(message.msg_type)
    if (!shouldPost(tier)) return false
    try {
      const notification = new Notification(
        `${(message.msg_type || '').toUpperCase()} · ${tier === 1 ? 'agent paused' : 'new message'}`,
        {
          body: [message.title, message.session_title].filter(Boolean).join('\n')
            || message.body || '',
          // One live notification per session — a later one replaces it rather
          // than stacking a column of near-identical banners.
          tag: message.trace_id || `msg-${message.id}`,
          renotify: tier === 1,
          requireInteraction: tier === 1,
          silent: tier !== 1,
        })
      notification.onclick = () => {
        window.focus()
        onOpen?.(message)
        notification.close()
      }
      lastPost.value = tier === 1
        ? 'Sent — blockers always post.'
        : 'Sent — the tab was hidden.'
      return true
    } catch (err) {
      // Chrome throws here on Android without a service worker, and Safari
      // before the page is installed to the Home Screen. Report it rather than
      // silently looking enabled.
      lastPost.value = `Blocked by the browser: ${err.message}`
      return false
    }
  }

  async function requestPermission() {
    if (!SUPPORTED) {
      permission.value = 'unsupported'
      return
    }
    if (Notification.permission === 'granted') {
      permission.value = 'granted'
      set('osEnabled', !prefs.osEnabled)
      return
    }
    // Must be called from a user gesture, which is why this is a button and
    // never fires on mount.
    const result = await Notification.requestPermission()
    permission.value = result
    set('osEnabled', result === 'granted')
  }

  return { supported: SUPPORTED, permission, lastPost, ready, post, requestPermission }
}
