<script setup>
// The secret stage (R12): a pause menu behind a triple click on ALL. HIGH
// SCORE reads the loop payload the caller fetched for the shell's scope, so
// every number on it is one the Overview shows too — labelled with that scope,
// because the payload counts everything ever settled there, not a week.
import { computed, nextTick, ref, watch } from 'vue'
import Button from '../../../../components/ui/Button.vue'
import Dialog from '../../../../components/ui/Dialog.vue'
import PixelSprite from '../PixelSprite.vue'

const open = defineModel('open', { type: Boolean, default: false })
const props = defineProps({
  // GET /api/pipeline/overview's `loop`; null while it is being read.
  loop: { type: Object, default: null },
  scopeLabel: { type: String, default: 'All projects' },
  error: { type: String, default: '' },
})

const view = ref('menu')
// Read off the DOM, not a v-for ref array: the entries are swapped wholesale
// between views, and such an array is not kept in render order.
const menu = ref(null)
const menuItems = () => [...(menu.value?.querySelectorAll('[role="menuitem"]') || [])]

const part = (stage, key) => props.loop?.stages
  ?.find(s => s.label === stage)?.parts?.find(p => p.key === key)?.n ?? 0
const score = computed(() => [
  { key: 'shipped', label: 'Features shipped', n: part('Features', 'shipped') },
  { key: 'done', label: 'Tasks done', n: part('Tasks', 'done') },
  { key: 'closed', label: 'Defects closed', n: part('Defects', 'closed') },
])

const MENU = [
  { key: 'continue', label: 'Continue' },
  { key: 'score', label: 'High score' },
  { key: 'credits', label: 'Credits' },
]
const entries = computed(() => (view.value === 'menu' ? MENU : [{ key: 'back', label: 'Back' }]))

async function focusFirst() {
  await nextTick()
  menuItems()[0]?.focus()
}

watch(open, (isOpen) => { if (isOpen) view.value = 'menu' })

function choose(key) {
  if (key === 'continue') open.value = false
  else if (key === 'back') view.value = 'menu'
  else view.value = key
  focusFirst()
}

function onKeydown(e) {
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
  e.preventDefault()
  const els = menuItems()
  const at = els.indexOf(document.activeElement)
  const next = (at + (e.key === 'ArrowDown' ? 1 : -1) + els.length) % els.length
  els[next]?.focus()
}
</script>

<template>
  <Dialog
    v-model:open="open"
    title="Secret stage"
    kicker="★-★ Paused"
    hint="↑↓ move · Enter select · Esc close"
    data-pl-part="dialog"
    data-pl-secret=""
    data-testid="secret-stage"
    @open-auto-focus="focusFirst"
  >
    <template #mark>
      <span class="ss-stage" aria-hidden="true">
        <PixelSprite name="coin" :scale="2" class="ss-coin ss-coin--1" />
        <PixelSprite name="coin" :scale="2" class="ss-coin ss-coin--2" />
        <PixelSprite name="bot" :scale="2" class="ss-bot" />
      </span>
    </template>

    <div v-if="view === 'score'" data-pl-part="score" data-testid="secret-board">
      <p data-pl-part="score-scope">High score · {{ scopeLabel }}</p>
      <p v-if="error" role="alert">{{ error }}</p>
      <p v-else-if="!loop" role="status">Reading the scoreboard…</p>
      <dl v-else>
        <div v-for="s in score" :key="s.key" data-pl-part="score-row">
          <dt>{{ s.label }}</dt>
          <dd :data-testid="`secret-score-${s.key}`">{{ s.n }}</dd>
        </div>
      </dl>
    </div>
    <div v-else-if="view === 'credits'" data-pl-part="score" data-testid="secret-roll">
      <p>Pipeline Level — a regin theme.</p>
      <p>Every bot, bug and flag on these screens is a real row.</p>
    </div>

    <div ref="menu" role="menu" aria-label="Secret stage" data-pl-part="menu">
      <Button
        v-for="(m, i) in entries"
        :key="m.key"
        variant="ghost"
        role="menuitem"
        data-pl-part="menu-item"
        :tabindex="i === 0 ? 0 : -1"
        :data-testid="`secret-${m.key}`"
        @click="choose(m.key)"
        @keydown="onKeydown"
      >{{ m.label }}</Button>
    </div>
    <!-- Empty on purpose: the footer is where the dialog draws its key hints. -->
    <template #footer />
  </Dialog>
</template>

<style scoped>
.ss-stage {
  position: relative;
  display: block;
  flex: none;
  width: 64px;
  height: 28px;
}

.ss-bot {
  position: absolute;
  bottom: 0;
  left: 0;
  animation: ss-run 1.6s steps(8, end) infinite alternate, ss-hop 0.4s steps(2, end) infinite;
}

.ss-coin {
  position: absolute;
  top: 0;
  animation: ss-coin 0.8s steps(2, end) infinite;
}

.ss-coin--1 { left: 22px; }
.ss-coin--2 { left: 44px; animation-delay: 0.4s; }

@keyframes ss-run {
  from { transform: translateX(0); }
  to { transform: translateX(40px); }
}

@keyframes ss-hop {
  50% { translate: 0 -4px; }
}

@keyframes ss-coin {
  50% { opacity: 0; }
}

@media (prefers-reduced-motion: reduce) {
  .ss-bot,
  .ss-coin {
    animation: none;
  }
}
</style>
