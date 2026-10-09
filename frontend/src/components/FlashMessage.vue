<script setup>
import { useFlash } from '../composables/useFlash'
import Button from './ui/Button.vue'

const { state, clear } = useFlash()

function run(action) {
  clear()
  action()
}
</script>

<template>
  <div
    v-if="state.message"
    role="status"
    aria-live="polite"
    :class="['alert', state.type === 'success' ? 'alert-success' : state.type === 'error' ? 'alert-error' : 'alert-info']"
    class="flex items-center justify-between"
    data-testid="flash"
    data-pl-part="toast"
    :data-tone="state.type"
  >
    <span>
      {{ state.message }}<template v-if="state.link?.action">
        · <Button
          variant="link"
          size="sm"
          class="flash-link flash-link--action"
          data-testid="flash-link"
          @click="run(state.link.action)"
        >{{ state.link.label }}</Button>
      </template><template v-else-if="state.link">
        · <router-link
          :to="state.link.to"
          class="flash-link"
          data-testid="flash-link"
          @click="clear"
        >{{ state.link.label }}</router-link>
      </template>
    </span>
    <Button variant="ghost" size="icon" class="ml-4" aria-label="Dismiss" @click="clear">&times;</Button>
  </div>
</template>

<style scoped>
.flash-link { font-weight: 600; color: inherit; text-decoration: underline; }
.flash-link--action { height: auto; padding: 0; }
</style>
