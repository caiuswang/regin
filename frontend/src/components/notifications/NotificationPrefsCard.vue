<script setup>
import { computed } from 'vue'
import Card from '../Card.vue'
import Button from '../ui/Button.vue'
import Input from '../ui/Input.vue'
import ToggleSwitch from '../ToggleSwitch.vue'
import { INBOX_TYPES, NOTIFICATION_TIERS } from '../../constants/inboxTypes'
import { useNotificationPrefs } from '../../composables/useNotificationPrefs'
import { useOsNotifications } from '../../composables/useOsNotifications'

// Browser-local, deliberately: OS permission is granted to an origin in one
// browser, and how loudly this machine should interrupt is a property of where
// you are sitting — not of the account. It is therefore NOT part of the
// server-side agent_messages block above, which configures the push channels.
const { prefs, set, LIMITS } = useNotificationPrefs()
const os = useOsNotifications()

const tiers = computed(() => [1, 2, 3].map(tier => ({
  tier,
  ...NOTIFICATION_TIERS[tier],
  types: INBOX_TYPES.filter(t => t.tier === tier).map(t => t.label).join(' · '),
})))

const osLabel = computed(() => {
  if (!os.supported) return 'Unsupported in this browser'
  if (os.permission.value === 'denied') return 'Permission denied'
  if (os.permission.value === 'granted') return prefs.osEnabled ? 'On' : 'Off'
  return 'Enable browser notifications'
})

const osStatus = computed(() => {
  if (!os.supported) return 'This browser has no Notification API — in-app surfaces only.'
  if (os.permission.value === 'denied') {
    return 'Denied. Reset it in the browser\'s site settings; the in-app banner '
      + 'still guarantees a blocker is seen.'
  }
  if (os.permission.value !== 'granted') {
    return 'Asks once, on your click. Blockers post even while the tab is in '
      + 'front; toasts post only while it is hidden; count-only never posts. '
      + 'iOS Safari needs the page installed to the Home Screen.'
  }
  return 'Granted for this browser.'
})
</script>

<template>
  <Card class="mt-4">
    <h3 class="np-title">Notifications on this device</h3>
    <p class="np-desc">
      How arriving messages interrupt you here. Stored in this browser, not on
      the server — it does not change what your agents send.
    </p>

    <table class="np-table">
      <tbody>
        <tr v-for="group in tiers" :key="group.tier">
          <th scope="row">Tier {{ group.tier }} · {{ group.label }}</th>
          <td>
            <div class="np-types">{{ group.types }}</div>
            <div class="np-blurb">{{ group.blurb }}</div>
          </td>
        </tr>

        <tr>
          <th scope="row">Show toasts</th>
          <td>
            <ToggleSwitch
              :model-value="prefs.toastsEnabled"
              @update:model-value="set('toastsEnabled', $event)"
            />
            <div class="np-blurb">
              Off leaves tier 2 counting silently into the badge. Tier 1 always
              shows — the agent is stopped until you answer it.
            </div>
          </td>
        </tr>

        <tr>
          <th scope="row">Open blockers automatically</th>
          <td>
            <ToggleSwitch
              :model-value="prefs.autoPopout"
              @update:model-value="set('autoPopout', $event)"
            />
            <div class="np-blurb">
              On, an arriving tier-1 decision opens its pop-out straight away
              instead of waiting for a click. Off by default: the modal takes
              focus. Never raised on Inbox or Live, where the queue is already
              on screen.
            </div>
          </td>
        </tr>

        <tr>
          <th scope="row">Toasts on screen</th>
          <td>
            <Input
              class="np-input"
              type="number"
              :model-value="prefs.maxToasts"
              @update:model-value="set('maxToasts', $event)"
            />
            <div class="np-blurb">
              {{ LIMITS.maxToasts[0] }}–{{ LIMITS.maxToasts[1] }}. The rest fold
              into the Inbox badge rather than stacking.
            </div>
          </td>
        </tr>

        <tr>
          <th scope="row">Toast duration</th>
          <td>
            <Input
              class="np-input"
              type="number"
              :model-value="prefs.toastDurationSec"
              @update:model-value="set('toastDurationSec', $event)"
            />
            <div class="np-blurb">
              Seconds ({{ LIMITS.toastDurationSec[0] }}–{{ LIMITS.toastDurationSec[1] }}),
              then it folds into the badge. Still unread either way.
            </div>
          </td>
        </tr>

        <tr>
          <th scope="row">OS notifications</th>
          <td>
            <Button
              size="sm"
              :variant="os.ready() ? 'primary' : 'secondary'"
              :disabled="!os.supported || os.permission.value === 'denied'"
              @click="os.requestPermission"
            >{{ osLabel }}</Button>
            <div class="np-blurb">{{ osStatus }}</div>
          </td>
        </tr>
      </tbody>
    </table>
  </Card>
</template>

<style scoped>
.np-title { font-size: 0.9rem; font-weight: 650; color: var(--color-fg); }

.np-desc {
  margin: 0.25rem 0 0.75rem;
  font-size: 0.8rem;
  color: var(--color-fg-muted);
  line-height: 1.55;
}

.np-table { width: 100%; border-collapse: collapse; }

.np-table th,
.np-table td {
  text-align: left;
  vertical-align: top;
  padding: 0.6rem 0.5rem;
  border-top: 1px solid var(--color-border-subtle);
}

.np-table th {
  width: 12rem;
  font-size: 0.8rem;
  font-weight: 550;
  color: var(--color-fg);
}

.np-types { font-size: 0.8rem; color: var(--color-fg); }

.np-blurb {
  margin-top: 0.25rem;
  font-size: 0.72rem;
  color: var(--color-fg-muted);
  line-height: 1.5;
}

.np-input { width: 6rem; }
</style>
