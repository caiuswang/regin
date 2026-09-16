<script setup>
// "New session" on a desktop page: the /live launch form in a modal instead of
// a bottom sheet. Same form, deliberately — starting a session is one act with
// one set of choices, and a second, narrower form on the session pages would be
// where the two drift, leaving the worktree option on one page and not the
// other.
//
// Button and dialog ship together rather than as a dialog the caller opens: the
// two views that host this would otherwise each carry the open flag, the click
// handler and the wiring between them, three times over for one behaviour.
//
// The form is not `v-if`'d — Reka's portal already unmounts closed content,
// which is what `useLaunchDraft` is built around: the draft outlives the
// unmount, so a dismissed dialog keeps what was typed for the reopen.
import { ref } from 'vue'
import Button from '../ui/Button.vue'
import Dialog from '../ui/Dialog.vue'
import LiveLaunchSheet from './LiveLaunchSheet.vue'

defineProps({
  // The session the page is open on, offered as the continuation target. '' on
  // the list, where there is no such context.
  resumeFrom: { type: String, default: '' },
  // The working directory the page is about, offered as the starting pick.
  initialCwd: { type: String, default: '' },
  // The surface's identity for the shared launch draft — see LiveLaunchSheet.
  scope: { type: String, default: '' },
  variant: { type: String, default: 'secondary' },
})
const emit = defineEmits(['launched'])

const open = ref(false)

function onLaunched(payload) {
  open.value = false
  emit('launched', payload)
}
</script>

<template>
  <Dialog
    v-model:open="open"
    title="New agent session"
    description="Starts a claude session regin owns — optionally in a fresh git worktree."
  >
    <template #trigger>
      <Button size="sm" :variant="variant" data-testid="new-session-open">
        New session
      </Button>
    </template>
    <LiveLaunchSheet
      :resume-from="resumeFrom"
      :initial-cwd="initialCwd"
      :scope="scope"
      @launched="onLaunched"
    />
  </Dialog>
</template>
