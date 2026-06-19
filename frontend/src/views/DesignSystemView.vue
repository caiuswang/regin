<script setup>
// Living showcase + manual-QA surface for the unified design system.
// Each primitive added in the rollout gets a section here so we can eyeball
// every variant/size/state in one place, in both light and dark themes.
import { ref } from 'vue'
import Button from '../components/ui/Button.vue'
import Input from '../components/ui/Input.vue'
import Textarea from '../components/ui/Textarea.vue'
import Select from '../components/ui/Select.vue'
import Checkbox from '../components/ui/Checkbox.vue'
import RadioGroup from '../components/ui/RadioGroup.vue'
import ToggleSwitch from '../components/ToggleSwitch.vue'
import Dialog from '../components/ui/Dialog.vue'
import Tooltip from '../components/ui/Tooltip.vue'
import Popover from '../components/ui/Popover.vue'
import DropdownMenu from '../components/ui/DropdownMenu.vue'
import Tabs from '../components/ui/Tabs.vue'
import Badge from '../components/Badge.vue'
import { severityColor } from '../composables/useBadgeColor'

const loadingDemo = ref(false)
function fakeWork() {
  loadingDemo.value = true
  setTimeout(() => (loadingDemo.value = false), 1500)
}

const VARIANTS = ['primary', 'secondary', 'danger', 'ghost', 'link']
const SIZES = ['sm', 'md', 'lg']

// Form-control demo state
const text = ref('hello')
const area = ref('multi-line\ntext')
const pick = ref('beta')
const checked = ref(true)
const choice = ref('overwrite')
const toggled = ref(true)
const SELECT_OPTS = [
  { value: 'alpha', label: 'Alpha' },
  { value: 'beta', label: 'Beta' },
  { value: 'gamma', label: 'Gamma' },
]
const RADIO_OPTS = ['skip', 'overwrite', 'rename']

// Overlay demo state
const dialogOpen = ref(false)
const popoverOpen = ref(false)
const lastMenuAction = ref('—')
const MENU_ITEMS = [
  { label: 'Edit', onSelect: () => (lastMenuAction.value = 'Edit') },
  { label: 'Duplicate', onSelect: () => (lastMenuAction.value = 'Duplicate') },
  { separator: true },
  { label: 'Delete', danger: true, onSelect: () => (lastMenuAction.value = 'Delete') },
]

// Tabs + badge demo state
const seg = ref('schema')
const underline = ref('templates')
const SEG_TABS = [
  { value: 'schema', label: 'Schema' },
  { value: 'diff', label: 'Diff' },
  { value: 'findings', label: 'Findings' },
]
const UL_TABS = [
  { value: 'templates', label: 'Templates' },
  { value: 'grader', label: 'Grader prompts' },
]
</script>

<template>
  <div class="ds-page">
    <header class="ds-head">
      <h1 class="text-xl font-semibold text-fg">Design System</h1>
      <p class="text-sm text-fg-subtle">
        Unified primitives. Toggle the app theme to verify dark mode.
      </p>
    </header>

    <section class="ds-section">
      <h2 class="ds-h2">Button — variants</h2>
      <div class="ds-row">
        <Button v-for="v in VARIANTS" :key="v" :variant="v">{{ v }}</Button>
      </div>
    </section>

    <section class="ds-section">
      <h2 class="ds-h2">Button — sizes</h2>
      <div class="ds-row">
        <Button v-for="s in SIZES" :key="s" variant="primary" :size="s">size {{ s }}</Button>
        <Button size="icon" variant="secondary" aria-label="Settings">⚙</Button>
      </div>
    </section>

    <section class="ds-section">
      <h2 class="ds-h2">Button — states</h2>
      <div class="ds-row">
        <Button variant="primary" disabled>disabled</Button>
        <Button variant="secondary" disabled>disabled</Button>
        <Button variant="primary" :loading="loadingDemo" @click="fakeWork">
          {{ loadingDemo ? 'Working…' : 'Click to load' }}
        </Button>
        <Button as="a" href="#" variant="link">rendered as &lt;a&gt;</Button>
      </div>
    </section>

    <section class="ds-section">
      <h2 class="ds-h2">Form controls</h2>
      <div class="ds-grid">
        <label class="ds-field">
          <span class="ds-field-label">Input</span>
          <Input v-model="text" placeholder="Type…" />
        </label>
        <label class="ds-field">
          <span class="ds-field-label">Select (block)</span>
          <Select v-model="pick" :options="SELECT_OPTS" placeholder="Pick one…" block />
        </label>
        <label class="ds-field">
          <span class="ds-field-label">Input (disabled)</span>
          <Input model-value="locked" disabled />
        </label>
        <label class="ds-field">
          <span class="ds-field-label">Input (error)</span>
          <Input model-value="bad value" error />
        </label>
      </div>
      <label class="ds-field ds-field-wide">
        <span class="ds-field-label">Textarea</span>
        <Textarea v-model="area" :rows="3" />
      </label>
      <div class="ds-row ds-controls-row">
        <Checkbox v-model="checked" label="Checkbox" />
        <ToggleSwitch v-model="toggled" on-label="On" off-label="Off" />
        <RadioGroup v-model="choice" :options="RADIO_OPTS" inline />
      </div>
      <p class="ds-note">
        By default <code>&lt;Select&gt;</code> shrinks to its widest option (capped
        at the container) so it never stretches wide in a toolbar — add
        <code>block</code> only when it should fill a form column. Both below sit in
        the same wide row:
      </p>
      <div class="ds-row ds-controls-row">
        <Select v-model="pick" :options="SELECT_OPTS" placeholder="Default (content-width)" />
        <span class="ds-block-demo"><Select v-model="pick" :options="SELECT_OPTS" placeholder="block — fills its container" block /></span>
      </div>
    </section>

    <section class="ds-section">
      <h2 class="ds-h2">Overlays</h2>
      <div class="ds-row">
        <Button variant="primary" @click="dialogOpen = true">Open dialog</Button>
        <Dialog
          v-model:open="dialogOpen"
          title="Unified dialog"
          description="Focus-trapped, scroll-locked, Escape + click-outside to close — all from Reka."
        >
          <p>Tab cycles inside; the page behind cannot scroll or be focused.</p>
          <template #footer>
            <Button variant="secondary" @click="dialogOpen = false">Cancel</Button>
            <Button variant="primary" @click="dialogOpen = false">Confirm</Button>
          </template>
        </Dialog>

        <Tooltip content="A real, accessible tooltip">
          <Button variant="secondary">Hover for tooltip</Button>
        </Tooltip>

        <Popover v-model:open="popoverOpen">
          <template #trigger>
            <Button variant="secondary">Open popover</Button>
          </template>
          <div class="ds-pop-body">
            <p class="text-sm text-fg">Popover content.</p>
            <Button size="sm" variant="primary" @click="popoverOpen = false">Got it</Button>
          </div>
        </Popover>

        <DropdownMenu :items="MENU_ITEMS">
          <template #trigger>
            <Button variant="ghost" size="icon" aria-label="More actions">⋯</Button>
          </template>
        </DropdownMenu>
        <span class="text-xs text-fg-subtle">menu picked: {{ lastMenuAction }}</span>
      </div>
    </section>

    <section class="ds-section">
      <h2 class="ds-h2">Tabs</h2>
      <div class="ds-tabs-demo">
        <Tabs v-model="seg" :tabs="SEG_TABS" variant="segmented" />
        <span class="text-xs text-fg-subtle">active: {{ seg }}</span>
      </div>
      <div class="ds-tabs-demo">
        <Tabs v-model="underline" :tabs="UL_TABS" variant="underline" />
        <span class="text-xs text-fg-subtle">active: {{ underline }}</span>
      </div>
    </section>

    <section class="ds-section">
      <h2 class="ds-h2">Badge — semantic colors (one source of truth)</h2>
      <div class="ds-row">
        <Badge color="green">ratified</Badge>
        <Badge color="yellow">pending</Badge>
        <Badge color="red">rejected</Badge>
        <Badge color="blue">proposed</Badge>
        <Badge color="purple">built-in</Badge>
        <Badge color="gray">draft</Badge>
        <Badge :color="severityColor('error')">severityColor('error')</Badge>
        <Badge :color="severityColor('warn')">severityColor('warn')</Badge>
      </div>
    </section>
  </div>
</template>

<style scoped>
.ds-page { padding: 1.5rem; max-width: 64rem; }
.ds-head { margin-bottom: 1.5rem; }
.ds-section {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  padding: 1.25rem;
  margin-bottom: 1rem;
}
.ds-h2 {
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-fg-subtle);
  margin-bottom: 0.875rem;
}
.ds-row { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; }
.ds-controls-row { gap: 1.5rem; margin-top: 1rem; }
.ds-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
  gap: 1rem;
}
.ds-field { display: flex; flex-direction: column; gap: 0.375rem; }
.ds-field-wide { margin-top: 1rem; }
.ds-field-label {
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--color-fg-muted);
}
.ds-note {
  font-size: 0.8125rem;
  line-height: 1.5;
  color: var(--color-fg-muted);
  margin: 1rem 0 0;
  max-width: 44rem;
}
.ds-note code {
  font-size: 0.75rem;
  padding: 0.05rem 0.3rem;
  border-radius: var(--radius-sm);
  background: var(--color-background);
  border: 1px solid var(--color-border);
}
.ds-block-demo { flex: 1; min-width: 0; }
.ds-pop-body { display: flex; flex-direction: column; gap: 0.5rem; }
.ds-tabs-demo { display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem; }
.ds-tabs-demo:last-child { margin-bottom: 0; }
</style>
