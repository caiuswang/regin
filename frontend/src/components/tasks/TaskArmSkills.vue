<script setup>
// The skills a run opens with, as an ordered pick: the first is the prompt's
// slash command — the CLI expands only one — and the rest are named after it
// for the agent to load. The menu is what the run's directory resolves, so a
// ticked name that directory lacks is kept, but said out loud.
import { computed, ref } from 'vue'
import Button from '../ui/Button.vue'
import Checkbox from '../ui/Checkbox.vue'
import Input from '../ui/Input.vue'

const props = defineProps({
  skills: { type: Array, required: true },
  args: { type: String, default: '' },
  catalog: { type: Array, default: () => [] },
  missing: { type: Function, required: true },
  cwd: { type: String, default: '' },
})
const emit = defineEmits(['update:skills', 'update:args'])

const FILTER_FROM = 8
const filter = ref('')
const typed = ref('')

const sourceOf = computed(() => new Map(props.catalog.map(s => [s.name, s.source])))

const unticked = computed(() => {
  const q = filter.value.trim().toLowerCase()
  return props.catalog
    .filter(s => !props.skills.includes(s.name))
    .filter(s => !q || s.name.toLowerCase().includes(q))
})

const missingNames = computed(() => props.skills.filter(name => props.missing(name)))
const where = computed(() => props.cwd || 'the server working directory')

function toggle(name, on) {
  const rest = props.skills.filter(n => n !== name)
  emit('update:skills', on ? [...rest, name] : rest)
}

function addTyped() {
  const name = typed.value.trim().replace(/^\/+/, '')
  typed.value = ''
  if (name && !props.skills.includes(name)) emit('update:skills', [...props.skills, name])
}
</script>

<template>
  <div class="arm-skills" data-testid="task-arm-skills">
    <span class="live-launch-lbl" id="task-arm-skills-lbl">
      skills <span class="arm-skills-note">— the first opens the run as <code>/&lt;skill&gt;</code>, the rest are loaded after it</span>
    </span>

    <ol v-if="skills.length" class="arm-skills-picked" aria-labelledby="task-arm-skills-lbl">
      <li v-for="(name, i) in skills" :key="name">
        <Checkbox :model-value="true" :data-testid="`task-arm-skill-picked-${name}`" @update:model-value="v => toggle(name, v)">
          <span class="arm-skills-order">{{ i === 0 ? 'opens' : `+${i}` }}</span>
          <span class="arm-skills-name">{{ name }}</span>
          <span v-if="sourceOf.get(name)" class="arm-skills-src">{{ sourceOf.get(name) }}</span>
          <span v-if="missing(name)" class="arm-skills-miss">not in this repo</span>
        </Checkbox>
      </li>
    </ol>

    <Input
      v-if="catalog.length > FILTER_FROM"
      v-model="filter"
      data-testid="task-arm-skill-filter"
      placeholder="filter skills"
      aria-label="Filter skills"
    />
    <ul v-if="unticked.length" class="arm-skills-list" aria-labelledby="task-arm-skills-lbl">
      <li v-for="s in unticked" :key="s.name">
        <Checkbox :model-value="false" :data-testid="`task-arm-skill-option-${s.name}`" @update:model-value="v => toggle(s.name, v)">
          <span class="arm-skills-name">{{ s.name }}</span>
          <span class="arm-skills-src">{{ s.source }}</span>
        </Checkbox>
      </li>
    </ul>
    <p v-else-if="!catalog.length" class="arm-skills-note" data-testid="task-arm-skills-none">
      no skills found for {{ where }} — the task body is sent as a plain prompt
    </p>

    <div class="arm-skills-add">
      <Input
        v-model="typed"
        data-testid="task-arm-skill-add"
        placeholder="another skill by name, e.g. my-plugin:skill"
        aria-label="Add a skill by name"
        @keydown.enter.prevent="addTyped"
      />
      <Button variant="ghost" size="sm" data-testid="task-arm-skill-add-btn" @click="addTyped">Add</Button>
    </div>

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

.arm-skills-picked,
.arm-skills-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin: 0;
    padding: 6px 10px;
    list-style: none;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
}

.arm-skills-list {
    max-height: 11rem;
    overflow-y: auto;
}

.arm-skills-picked li,
.arm-skills-list li {
    padding: 6px 0;
    min-width: 0;
}

.arm-skills-name {
    font-family: var(--font-mono);
    font-size: 0.8125rem;
    overflow-wrap: anywhere;
}

.arm-skills-order,
.arm-skills-src {
    font-size: 0.75rem;
    color: var(--color-fg-muted);
}

.arm-skills-order {
    min-width: 2.5em;
    font-weight: 600;
}

.arm-skills-miss {
    font-size: 0.75rem;
    color: var(--color-warning-strong);
}

.arm-skills-add {
    display: flex;
    gap: 6px;
    align-items: center;
}
</style>
