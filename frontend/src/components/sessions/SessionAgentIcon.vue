<script setup>
import { computed } from 'vue'
import { agentTypeLabel, agentTypeTone } from '../../utils/sessionRowFormat.js'

const props = defineProps({
  s: { type: Object, required: true },
  size: { type: String, default: 'md' },  // 'md' (list) | 'sm' (card)
})

const label = computed(() => agentTypeLabel(props.s))
const tone = computed(() => agentTypeTone(props.s))

// Official CodeBuddy/WorkBuddy mascot mark, taken verbatim from the CodeBuddy
// Code web-ui pwa icon (filled rounded square, cat knocked out via evenodd).
// Both brands share the mascot; only the chip tint differs.
const BUDDY_MARK_PATH =
  'M0.0001 5.9182L0.0001 6.0001L0.0001 6.0821C0 8.8444 0 10.2426 0.8788 11.1214C1.0848 11.3274 1.3193 11.4851 1.5932 11.6058C1.5625 11.2148 1.6512 10.8775 1.855 10.6444C1.9344 10.5539 1.9369 10.5501 1.9196 10.398C1.8909 10.149 1.878 9.7805 1.891 9.5427L1.9014 9.3205L1.5679 8.7305C1.0513 7.8115 0.7233 7.0398 0.5967 6.4501C0.5299 6.1268 0.534 5.9834 0.6161 5.8773C0.6661 5.8132 0.8298 5.7469 1.0273 5.7104C1.5245 5.6231 2.6087 5.7021 3.815 5.9151L3.9403 5.9367L4.2156 5.6931C4.6728 5.2883 4.9764 5.0612 5.5363 4.7122C6.1198 4.3471 6.7783 4.0468 7.5198 3.809L7.7578 3.7327L7.8885 3.3893C8.3568 2.1531 8.8364 1.2418 9.1784 0.9386C9.296 0.8331 9.3031 0.829 9.3894 0.8238C9.5292 0.8136 9.6572 0.8807 9.8752 1.0791C10.3844 1.5418 11.0934 2.4931 11.5342 3.3054L11.7045 3.6207L11.9451 3.7403C11.955 3.7453 11.9652 3.7505 11.9756 3.756C11.9193 2.3548 11.7335 1.4908 11.1214 0.8788C10.2426 0 8.8444 0 6.0821 0.0001L6.0001 0.0001L5.9182 0.0001C3.1558 0 1.7576 0 0.8788 0.8788C0 1.7576 0 3.1558 0.0001 5.9182ZM12.0001 5.5869C11.3059 4.9017 10.2729 4.6581 9.3399 4.9737C9.0955 5.0564 8.8304 5.2094 8.3004 5.5154L5.2558 7.2732C4.7258 7.5792 4.4608 7.7322 4.2669 7.9026C3.481 8.5933 3.1852 9.6974 3.5204 10.6886C3.6031 10.9331 3.7561 11.1981 4.0621 11.7281C4.1171 11.8234 4.1672 11.9101 4.2133 11.9894C4.7159 12.0002 5.2807 12.0001 5.9182 12.0001L6.0001 12.0001L6.0821 12.0001C8.8444 12.0002 10.2426 12.0003 11.1214 11.1214C12.0003 10.2426 12.0002 8.8444 12.0001 6.0821L12.0001 6.0001L12.0001 5.9182C12.0001 5.8055 12.0001 5.6951 12.0001 5.5869ZM9.3152 7.2263Q9.5309 7.1018 9.7715 7.1662Q10.0122 7.2307 10.1367 7.4464L10.7843 8.5681Q10.9089 8.7838 10.8444 9.0245Q10.7799 9.2651 10.5642 9.3896Q10.4606 9.4494 10.342 9.465Q10.2234 9.4806 10.1079 9.4497Q9.9923 9.4187 9.8974 9.3459Q9.8025 9.2731 9.7427 9.1695L9.0951 8.0478Q9.0353 7.9442 9.0197 7.8256Q9.0041 7.707 9.035 7.5915Q9.066 7.4759 9.1388 7.381Q9.2116 7.2861 9.3152 7.2263ZM6.5262 9.0398Q6.4107 9.0088 6.2921 9.0244Q6.1735 9.0401 6.0699 9.0999Q5.9663 9.1597 5.8935 9.2546Q5.8207 9.3495 5.7897 9.465Q5.7588 9.5806 5.7744 9.6992Q5.79 9.8177 5.8498 9.9213L6.4974 11.043Q6.6219 11.2588 6.8625 11.3232Q7.1031 11.3877 7.3189 11.2632Q7.4225 11.2033 7.4953 11.1084Q7.5681 11.0135 7.5991 10.898Q7.63 10.7825 7.6144 10.6639Q7.5988 10.5453 7.539 10.4417L6.8914 9.32Q6.8316 9.2164 6.7367 9.1436Q6.6418 9.0708 6.5262 9.0398Z'
</script>

<template>
  <span
    class="agent-icon"
    :class="[`agent-icon--${tone}`, `agent-icon--size-${size}`]"
    :title="label"
    :aria-label="label"
    role="img"
  >
    <svg v-if="tone === 'workflow'" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="3.2" cy="8" r="1.5" />
      <path d="M4.7 8h2.8M7.5 4v8M7.5 4h3M7.5 8h3M7.5 12h3" />
      <circle cx="12" cy="4" r="1.3" />
      <circle cx="12" cy="8" r="1.3" />
      <circle cx="12" cy="12" r="1.3" />
    </svg>
    <svg v-else-if="tone === 'llm-stage'" viewBox="0 0 16 16" aria-hidden="true">
      <rect x="2.5" y="2.5" width="11" height="11" rx="2" />
      <path d="M5.5 8.5 7 10l3.5-3.5" />
    </svg>
    <svg v-else-if="tone === 'claude'" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M8 2.2 9.5 6.5 13.8 8 9.5 9.5 8 13.8 6.5 9.5 2.2 8 6.5 6.5 8 2.2Z" />
    </svg>
    <svg v-else-if="tone === 'codex'" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M3 3.5h10a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1Zm2.2 3L7 8 5.2 9.5M8.2 10h3" />
    </svg>
    <svg v-else-if="tone === 'kimi'" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M5 3v10M5 8l5-5M5 8.5l5 4.5" />
    </svg>
    <svg v-else-if="tone === 'codebuddy' || tone === 'workbuddy'" viewBox="0 0 12 12" aria-hidden="true">
      <path :d="BUDDY_MARK_PATH" fill="currentColor" fill-rule="evenodd" stroke="none" />
    </svg>
    <svg v-else viewBox="0 0 16 16" aria-hidden="true">
      <path d="M8 2.5a5.5 5.5 0 1 1 0 11 5.5 5.5 0 0 1 0-11Zm0 2.2v3.1l2.2 2.1" />
    </svg>
  </span>
</template>

<style scoped>
.agent-icon {
  align-items: center;
  border-radius: 0.625rem;
  display: inline-flex;
  flex: 0 0 auto;
  justify-content: center;
}
.agent-icon--size-md { height: 1.75rem; width: 1.75rem; }
.agent-icon--size-sm { height: 1.375rem; width: 1.375rem; border-radius: 0.5rem; }
.agent-icon svg {
  fill: none;
  stroke: currentColor;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.6;
}
.agent-icon--size-md svg { height: 1rem; width: 1rem; }
.agent-icon--size-sm svg { height: 0.8125rem; width: 0.8125rem; }

.agent-icon--claude { background: var(--color-orange-50); color: var(--color-orange-700); }
.agent-icon--codex { background: var(--color-indigo-50); color: var(--color-indigo-700); }
.agent-icon--kimi { background: var(--color-purple-50); color: var(--color-purple-700); }
.agent-icon--codebuddy { background: var(--color-blue-50); color: var(--color-blue-700); }
.agent-icon--workbuddy { background: var(--color-cyan-50); color: var(--color-cyan-700); }
.agent-icon--generic { background: var(--color-surface-2); color: var(--color-fg-subtle); }
.agent-icon--workflow { background: var(--color-emerald-50); color: var(--color-teal-700); }
.agent-icon--llm-stage { background: var(--color-sky-50); color: var(--color-sky-700); }
</style>
