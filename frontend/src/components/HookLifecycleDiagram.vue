<script setup>
import '@vue-flow/core/dist/style.css'
import { computed, ref } from 'vue'
import { VueFlow } from '@vue-flow/core'
import Badge from './Badge.vue'
import ToggleSwitch from './ToggleSwitch.vue'

const props = defineProps({
  handlers: { type: Array, default: () => [] },
  handlersByEvent: { type: Object, default: () => ({}) },
  handlerLoading: { type: Object, default: () => ({}) },
  selectedProvider: { type: String, default: '' },
})

const emit = defineEmits(['toggle-handler', 'set-priority', 'reset-priority'])

// --- Static graph definition ---

const MAIN_X = 220
const SIDE_X = 20
const W_MAIN = 200
const W_SIDE = 160

function mainNode(id, label, events, y, isSpecial = null) {
  return { id, type: 'lifecycle', position: { x: MAIN_X, y }, style: { width: `${W_MAIN}px` }, data: { label, events, isSide: false, isSpecial } }
}
function sideNode(id, label, events, y) {
  return { id, type: 'lifecycle', position: { x: SIDE_X, y }, style: { width: `${W_SIDE}px` }, data: { label, events, isSide: true, isSpecial: null } }
}

const LIFECYCLE_NODES = [
  mainNode('session-start',    'Session Start',                    ['SessionStart'],                               0,    'start'),
  mainNode('prompt-submit',    'UserPromptSubmit',                  ['UserPromptSubmit'],                           130),
  mainNode('pre-tool',         'PreToolUse',                        ['PreToolUse'],                                 250),
  mainNode('perm-request',     'PermissionRequest',                 ['PermissionRequest'],                          370),
  mainNode('post-tool',        'PostToolUse / PostToolUseFailure',  ['PostToolUse', 'PostToolUseFailure'],          490),
  mainNode('subagent',         'SubagentStart / SubagentStop',      ['SubagentStart', 'SubagentStop'],              610),
  mainNode('task',             'TaskCreated / TaskCompleted',       ['TaskCreated', 'TaskCompleted'],               730),
  mainNode('stop',             'Stop / StopFailure',                ['Stop', 'StopFailure'],                        850,  'stop'),
  mainNode('teammate-idle',    'TeammateIdle',                      ['TeammateIdle'],                               990),
  mainNode('pre-compact',      'PreCompact',                        ['PreCompact'],                                 1100),
  mainNode('post-compact',     'PostCompact',                       ['PostCompact'],                                1210),
  mainNode('session-end',      'Session End',                       ['SessionEnd'],                                 1320),

  sideNode('setup',            'Setup (Opt-in)',                    ['Setup'],                                      0),
  sideNode('expansion',        'UserPromptExpansion',               ['UserPromptExpansion'],                        130),
  sideNode('perm-denied',      'PermissionDenied',                  ['PermissionDenied'],                           370),
  sideNode('elicitation',      'Elicitation / Result',              ['Elicitation', 'ElicitationResult'],           490),
  sideNode('notification',     'Notification (Async)',              ['Notification'],                               850),
  sideNode('config-change',    'ConfigChange',                      ['ConfigChange'],                               990),
  sideNode('worktree',         'WorktreeCreate / Remove',           ['WorktreeCreate', 'WorktreeRemove'],           1100),
  sideNode('cwd',              'CwdChanged / FileChanged',          ['CwdChanged', 'FileChanged'],                  1210),
  sideNode('instructions',     'InstructionsLoaded',                ['InstructionsLoaded'],                         1320),
]

// Directed edges for the main lifecycle flow
const LIFECYCLE_EDGES = [
  { id: 'e1',  source: 'session-start',  target: 'prompt-submit',  type: 'smoothstep', animated: false, style: { stroke: '#9ca3af' } },
  { id: 'e2',  source: 'prompt-submit',  target: 'pre-tool',       type: 'smoothstep', animated: false, style: { stroke: '#9ca3af' } },
  { id: 'e3',  source: 'pre-tool',       target: 'perm-request',   type: 'smoothstep', animated: false, style: { stroke: '#9ca3af' } },
  { id: 'e4',  source: 'perm-request',   target: 'post-tool',      type: 'smoothstep', animated: false, style: { stroke: '#9ca3af' } },
  { id: 'e5',  source: 'post-tool',      target: 'subagent',       type: 'smoothstep', animated: false, style: { stroke: '#9ca3af' } },
  { id: 'e6',  source: 'subagent',       target: 'task',           type: 'smoothstep', animated: false, style: { stroke: '#9ca3af' } },
  { id: 'e7',  source: 'task',           target: 'stop',           type: 'smoothstep', animated: false, style: { stroke: '#9ca3af' } },
  { id: 'e8',  source: 'stop',           target: 'teammate-idle',  type: 'smoothstep', animated: false, style: { stroke: '#d1d5db' } },
  { id: 'e9',  source: 'teammate-idle',  target: 'pre-compact',    type: 'smoothstep', animated: false, style: { stroke: '#d1d5db' } },
  { id: 'e10', source: 'pre-compact',    target: 'post-compact',   type: 'smoothstep', animated: false, style: { stroke: '#d1d5db' } },
  { id: 'e11', source: 'post-compact',   target: 'session-end',    type: 'smoothstep', animated: false, style: { stroke: '#d1d5db' } },
  // side connections (dashed)
  { id: 'es1', source: 'setup',          target: 'session-start',  type: 'smoothstep', style: { stroke: '#d1d5db', strokeDasharray: '4 3' } },
  { id: 'es2', source: 'expansion',      target: 'prompt-submit',  type: 'smoothstep', style: { stroke: '#d1d5db', strokeDasharray: '4 3' } },
  { id: 'es3', source: 'perm-denied',    target: 'perm-request',   type: 'smoothstep', style: { stroke: '#d1d5db', strokeDasharray: '4 3' } },
  { id: 'es4', source: 'elicitation',    target: 'post-tool',      type: 'smoothstep', style: { stroke: '#d1d5db', strokeDasharray: '4 3' } },
  { id: 'es5', source: 'notification',   target: 'stop',           type: 'smoothstep', style: { stroke: '#d1d5db', strokeDasharray: '4 3' } },
]

// --- Reactive enrichment ---

const enrichedNodes = computed(() =>
  LIFECYCLE_NODES.map(node => {
    const all = node.data.events.flatMap(ev => props.handlersByEvent[ev] || [])
    const enabled = all.filter(h => h.enabled)
    return {
      ...node,
      data: {
        ...node.data,
        enabledCount: enabled.length,
        totalCount: all.length,
        kinds: [...new Set(enabled.map(h => h.kind))],
      },
    }
  })
)

// --- Node click → detail panel ---

const selectedNode = ref(null)

const selectedHandlers = computed(() => {
  if (!selectedNode.value) return []
  // Dedupe by name: a handler registered to multiple events
  // (`turn_trace`) shouldn't appear twice on a node that covers >1 event.
  const seen = new Set()
  const merged = []
  for (const ev of selectedNode.value.data.events) {
    for (const h of (props.handlersByEvent[ev] || [])) {
      if (seen.has(h.name)) continue
      seen.add(h.name)
      merged.push(h)
    }
  }
  merged.sort((a, b) => a.priority - b.priority || a.name.localeCompare(b.name))
  return merged
})

function onNodeClick({ node }) {
  selectedNode.value = selectedNode.value?.id === node.id ? null : node
}

// --- Inline priority edit ---
//
// Each row's priority input is uncontrolled-but-seeded: the displayed
// value is the handler's current effective priority; on blur or Enter
// we emit `set-priority` to the parent if the user's input differs
// from what's currently on screen. Parent issues the API call and
// refetches — the new prop value flows back through `selectedHandlers`
// and updates the field naturally.

function onPriorityChange(handler, event) {
  const raw = event.target.value
  const next = Number(raw)
  if (!Number.isFinite(next) || Number.isNaN(next)) {
    // Reset the field to the canonical value if the user typed junk.
    event.target.value = String(handler.priority)
    return
  }
  const clamped = Math.max(0, Math.min(9999, Math.round(next)))
  if (clamped === handler.priority) {
    event.target.value = String(handler.priority)
    return
  }
  emit('set-priority', { name: handler.name, priority: clamped })
}

// --- Node style helpers ---


function nodeStyle(data) {
  if (data.isSpecial === 'start') return { border: '2px solid #16a34a', background: '#dcfce7' }
  if (data.isSpecial === 'stop')  return { border: '2px solid #dc2626', background: '#fee2e2' }
  if (data.enabledCount > 0) {
    if (data.kinds.includes('gate')) return { border: '2px solid #d97706', background: '#fffbeb' }
    return { border: '2px solid #2563eb', background: '#eff6ff' }
  }
  if (data.totalCount > 0) return { border: '1px solid #d1d5db', background: '#f9fafb' }
  return { border: '1px solid #e5e7eb', background: '#ffffff' }
}
</script>

<template>
  <div class="flex overflow-hidden border border-gray-200 rounded-lg" style="height: 560px">
    <!-- Canvas: fills available width; nodes sit on the left, dot-grid extends right -->
    <div class="relative flex-1" style="height: 560px">
      <VueFlow
        :nodes="enrichedNodes"
        :edges="LIFECYCLE_EDGES"
        :nodes-connectable="false"
        :nodes-draggable="false"
        :zoom-on-scroll="true"
        :default-viewport="{ x: 64, y: 56, zoom: 0.92 }"
        :min-zoom="0.25"
        :max-zoom="2"
        @node-click="onNodeClick"
      >
        <template #node-lifecycle="{ data }">
          <div
            class="rounded-md px-3 py-2 cursor-pointer select-none transition-shadow focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-500 focus-visible:outline-offset-1"
            :class="[data.isSide ? 'text-xs' : 'text-sm font-medium']"
            :style="[nodeStyle(data), data.isSide ? { borderStyle: 'dashed' } : {}]"
          >
            <div :class="data.isSide ? 'text-gray-500' : 'text-gray-800'">{{ data.label }}</div>
            <div v-if="data.totalCount > 0 && !data.isSide" class="flex flex-wrap gap-1 mt-1.5">
              <span
                v-for="kind in data.kinds"
                :key="kind"
                class="inline-block text-[10px] px-1.5 py-0.5 rounded font-medium"
                :class="{
                  'bg-red-100 text-red-700':       kind === 'gate',
                  'bg-blue-100 text-blue-700':     kind === 'enrich',
                  'bg-gray-100 text-gray-600':     kind === 'trace',
                  'bg-purple-100 text-purple-700': kind === 'notify',
                }"
              >{{ kind }}</span>
              <span v-if="data.enabledCount === 0" class="text-[10px] text-gray-400">all disabled</span>
            </div>
            <div v-else-if="data.totalCount === 0 && !data.isSide" class="text-[10px] text-gray-300 mt-0.5">no handlers</div>
          </div>
        </template>
      </VueFlow>
    </div>

    <!-- Detail panel: slides in as flex sidebar when a node is selected -->
    <transition name="panel">
      <div
        v-if="selectedNode"
        class="flex-none w-72 border-l border-gray-200 bg-white flex flex-col"
      >
        <div class="flex items-center justify-between px-3 py-2 border-b border-gray-100 bg-gray-50 flex-none">
          <span class="text-xs font-semibold text-gray-700">{{ selectedNode.data.label }}</span>
          <button
            type="button"
            class="text-gray-400 hover:text-gray-600 text-base leading-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-500 focus-visible:rounded"
            @click="selectedNode = null"
          >×</button>
        </div>
        <div class="flex-1 overflow-y-auto p-3 space-y-1">
          <div v-if="!selectedHandlers.length" class="text-xs text-gray-400">No handlers registered for this event.</div>
          <p v-else class="text-[10px] text-gray-400 mb-1.5 leading-snug">
            Lower priority runs earlier in the chain. Edit the number to change order; press Enter or click away to save.
          </p>
          <div
            v-for="h in selectedHandlers"
            :key="h.name"
            class="flex items-start gap-2 py-1.5 border-b border-gray-50 last:border-0"
          >
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-1.5 flex-wrap">
                <span class="text-xs font-medium text-gray-800">{{ h.label }}</span>
                <span
                  class="text-[10px] px-1.5 py-0.5 rounded font-medium"
                  :class="{
                    'bg-red-100 text-red-700':       h.kind === 'gate',
                    'bg-blue-100 text-blue-700':     h.kind === 'enrich',
                    'bg-gray-100 text-gray-600':     h.kind === 'trace',
                    'bg-purple-100 text-purple-700': h.kind === 'notify',
                  }"
                >{{ h.kind }}</span>
                <span
                  v-if="h.events.length > 1"
                  class="text-[10px] text-amber-700 bg-amber-50 px-1 rounded"
                  :title="`Also fires on: ${h.events.filter(e => !selectedNode.data.events.includes(e)).join(', ') || h.events.join(', ')}`"
                >multi-event</span>
              </div>
              <div class="text-[10px] text-gray-400 mt-0.5 flex items-center gap-1.5 flex-wrap">
                <label class="flex items-center gap-1">
                  priority
                  <input
                    type="number"
                    min="0"
                    max="9999"
                    :value="h.priority"
                    :key="`${h.name}-${h.priority}`"
                    class="w-16 px-1 py-0.5 border border-gray-200 rounded text-[11px] text-gray-700 focus:outline-none focus:border-blue-400"
                    @change="onPriorityChange(h, $event)"
                    @keydown.enter.prevent="onPriorityChange(h, $event); $event.target.blur()"
                  />
                </label>
                <span v-if="h.priority_overridden" class="text-gray-400">· default {{ h.default_priority }}</span>
                <button
                  v-if="h.priority_overridden"
                  type="button"
                  class="text-blue-600 hover:text-blue-800 underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-500 focus-visible:rounded"
                  @click="emit('reset-priority', h.name)"
                >reset</button>
              </div>
            </div>
            <ToggleSwitch
              :model-value="h.enabled"
              :loading="!!handlerLoading[`${selectedProvider}:${h.name}`]"
              :disabled="!h.wired"
              on-label="on"
              :off-label="h.wired ? 'off' : 'not wired'"
              @change="emit('toggle-handler', h.name)"
            />
          </div>
        </div>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.panel-enter-active, .panel-leave-active { transition: opacity 0.15s, transform 0.15s; }
.panel-enter-from, .panel-leave-to { opacity: 0; transform: translateX(-8px); }

:deep(.vue-flow__pane) {
  background-color: #f8fafc;
  background-image: radial-gradient(#cbd5e1 1px, transparent 1px);
  background-size: 20px 20px;
}

/* tell Vue Flow the node dimensions without hard-coding width on the element */
:deep(.vue-flow__node-lifecycle) {
  padding: 0;
  border: none;
  background: transparent;
  border-radius: 6px;
}
</style>
