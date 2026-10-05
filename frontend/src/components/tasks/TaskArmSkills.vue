<script setup>
// The skills a run opens with, as an ordered pick: the first is the prompt's
// slash command — the CLI expands only one — and the rest are named after it
// for the agent to load. The menu is what the run's directory resolves, so a
// picked name that directory lacks is kept, but said out loud. One menu adds,
// the list below it orders and removes.
import { computed, ref } from 'vue'
import Button from '../ui/Button.vue'
import Input from '../ui/Input.vue'
import Select from '../ui/Select.vue'

const props = defineProps({
  skills: { type: Array, required: true },
  args: { type: String, default: '' },
  catalog: { type: Array, default: () => [] },
  missing: { type: Function, required: true },
  cwd: { type: String, default: '' },
})
const emit = defineEmits(['update:skills', 'update:args'])

const BY_NAME = '__by_name__'
const byName = ref(false)
// Reka keeps its own copy of an unset value and emits only when that copy
// changes, so re-picking the last pick would be silent; a fresh Select per pick
// starts with nothing chosen.
const menuKey = ref(0)
const typed = ref('')

const sourceOf = computed(() => new Map(props.catalog.map(s => [s.name, s.source])))

const menu = computed(() => {
  const offered = props.catalog
    .filter(s => !props.skills.includes(s.name))
    .map(s => ({ value: s.name, label: s.name, count: s.source }))
  return [
    ...offered,
    ...(offered.length ? [{ separator: true }] : []),
    { value: BY_NAME, label: 'another skill by name…' },
  ]
})

const missingNames = computed(() => props.skills.filter(name => props.missing(name)))
const where = computed(() => props.cwd || 'the server working directory')

function add(name) {
  if (name && !props.skills.includes(name)) emit('update:skills', [...props.skills, name])
}

function pick(value) {
  menuKey.value++
  if (value === BY_NAME) {
    byName.value = true
    return
  }
  add(value)
}

function remove(name) {
  emit('update:skills', props.skills.filter(n => n !== name))
}

function addTyped() {
  const name = typed.value.trim().replace(/^\/+/, '')
  typed.value = ''
  if (!name) return
  add(name)
  byName.value = false
}
</script>

<template>
  <div class="arm-skills" data-testid="task-arm-skills">
    <label class="live-launch-lbl" for="task-arm-skill">
      skills <span class="arm-skills-note">— the first opens the run as <code>/&lt;skill&gt;</code>, the rest are loaded after it</span>
    </label>
    <Select
      id="task-arm-skill"
      :key="menuKey"
      :model-value="null"
      block
      :options="menu"
      :placeholder="skills.length ? 'add another skill…' : 'pick a skill…'"
      data-testid="task-arm-skill"
      @update:model-value="pick"
    />
    <p v-if="!catalog.length" class="arm-skills-note" data-testid="task-arm-skills-none">
      no skills found for {{ where }} — the task body is sent as a plain prompt
    </p>

    <div v-if="byName" class="arm-skills-add">
      <Input
        v-model="typed"
        data-testid="task-arm-skill-add"
        placeholder="skill name, e.g. my-plugin:skill"
        aria-label="Skill name"
        @keydown.enter.prevent="addTyped"
      />
      <Button variant="ghost" size="sm" data-testid="task-arm-skill-add-btn" @click="addTyped">Add</Button>
    </div>

    <ol v-if="skills.length" class="arm-skills-picked" aria-label="Picked skills">
      <li v-for="(name, i) in skills" :key="name" :data-testid="`task-arm-skill-picked-${name}`">
        <span class="arm-skills-order">{{ i === 0 ? 'opens' : `+${i}` }}</span>
        <span class="arm-skills-name">{{ name }}</span>
        <span v-if="sourceOf.get(name)" class="arm-skills-src">{{ sourceOf.get(name) }}</span>
        <span v-if="missing(name)" class="arm-skills-miss">not in this repo</span>
        <Button
          variant="ghost"
          size="sm"
          class="arm-skills-remove"
          :data-testid="`task-arm-skill-remove-${name}`"
          :aria-label="`Remove ${name}`"
          @click="remove(name)"
        >×</Button>
      </li>
    </ol>

    <Input
      v-if="skills.length"
      :model-value="args"
      data-testid="task-arm-skill-args"
      placeholder="arguments for the first skill, before the task body"
      aria-label="Skill arguments"
      @update:model-value="v => emit('update:args', v)"
    />
    <p v-if="missingNames.length" class="live-launch-warn" role="status" data-testid="task-arm-skill-missing">
      {{ missingNames.join(', ') }} {{ missingNames.length === 1 ? 'is' : 'are' }} not found in {{ where }};
      a run there cannot open with {{ missingNames.length === 1 ? 'it' : 'them' }}.
    </p>
  </div>
</template>

<style scoped>
.arm-skills {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
}

.arm-skills-note {
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
}

.arm-skills-picked {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin: 0;
    padding: 0;
    list-style: none;
}

.arm-skills-picked li {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    padding: 4px 4px 4px 10px;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
}

.arm-skills-name {
    font-family: var(--font-mono);
    font-size: 0.8125rem;
    min-width: 0;
    overflow-wrap: anywhere;
}

.arm-skills-order,
.arm-skills-src {
    flex: none;
    font-size: 0.75rem;
    color: var(--color-fg-muted);
}

.arm-skills-order {
    min-width: 3.25em;
    font-weight: 600;
}

.arm-skills-miss {
    font-size: 0.75rem;
    color: var(--color-warning-strong);
}

.arm-skills-remove {
    flex: none;
    margin-left: auto;
}

.arm-skills-add {
    display: flex;
    gap: 6px;
    align-items: center;
}
</style>
