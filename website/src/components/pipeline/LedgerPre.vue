<script setup>
import { LEDGER } from '../../content/pipeline.js'
</script>

<!-- One line per template line would leak indentation into the <pre>. -->
<template>
  <pre class="block-code"><template v-for="(line, i) in LEDGER" :key="i"><span v-if="line.prompt" class="pl-prompt">$ </span><span :class="{ 'pl-ok': line.ok }">{{ line.t }}</span>{{ i < LEDGER.length - 1 ? '\n' : '' }}</template></pre>
</template>
