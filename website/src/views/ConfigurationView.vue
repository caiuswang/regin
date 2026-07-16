<script setup>
import DocPage from '../components/DocPage.vue'
import CodeBlock from '../components/CodeBlock.vue'
import Callout from '../components/Callout.vue'
import DataTable from '../components/DataTable.vue'
import { CORE_SETTINGS, ENV_VARS, NESTED_BLOCKS } from '../content/settings.js'

const TOC = [
  { id: 'files', label: 'Files & precedence' },
  { id: 'core', label: 'Core settings' },
  { id: 'env', label: 'Environment variables' },
  ...NESTED_BLOCKS.map((b) => ({ id: b.id, label: b.title })),
]

const SETTING_COLUMNS = [
  { key: 'key', label: 'Key', code: true },
  { key: 'def', label: 'Default', code: true },
  { key: 'desc', label: 'Description' },
]
const ENV_COLUMNS = [
  { key: 'key', label: 'Variable', code: true },
  { key: 'desc', label: 'Effect' },
]
</script>

<template>
  <DocPage
    title="Configuration"
    lead="Everything regin reads at boot comes from one typed settings model (lib/settings.py, pydantic-settings). This page covers the files, the precedence chain, and every block you can tune."
    :toc="TOC"
  >
    <h2 id="files">Files &amp; precedence</h2>
    <p>Two JSON files plus environment variables, merged highest-wins:</p>
    <CodeBlock :code="'REGIN_* environment variable\n  > config/settings.local.json   (machine-local, gitignored)\n  > config/settings.json          (shared, git-tracked)\n  > field default                 (derived from REGIN_DATA_DIR / XDG_DATA_HOME / ~)'" />
    <p>Team-wide settings belong in <code>config/settings.json</code>; machine-specific paths, mode, provider choice, and secrets belong in <code>config/settings.local.json</code>. The <code>/settings</code> page of the web UI edits both, routing each key to the right file automatically.</p>
    <Callout tone="info">
      Settings edited in the web UI take effect in the running server immediately —
      <code>save_settings()</code> refreshes the process-wide singleton in place.
    </Callout>

    <h2 id="core">Core settings</h2>
    <DataTable :columns="SETTING_COLUMNS" :rows="CORE_SETTINGS" />

    <h2 id="env">Environment variables</h2>
    <p>Any settings key can be set as <code>REGIN_&lt;KEY&gt;</code>; these are the ones you'll actually reach for:</p>
    <DataTable :columns="ENV_COLUMNS" :rows="ENV_VARS" />

    <template v-for="block in NESTED_BLOCKS" :key="block.id">
      <h2 :id="block.id">{{ block.title }}</h2>
      <p><code>{{ block.key }}</code> — {{ block.summary }}</p>
      <DataTable :columns="SETTING_COLUMNS" :rows="block.rows" />
    </template>

    <Callout tone="warn">
      The agent-bridge <code>token</code> is equivalent to SSH access to the machine.
      Keep it (and the whole <code>agent_bridge</code> block) in the gitignored
      <code>config/settings.local.json</code>, never in the tracked <code>config/settings.json</code>.
    </Callout>
    <p>These tables list the settings you're most likely to touch; the exhaustive, always-current source is the <code>Settings</code> model in <code>lib/settings.py</code>, which documents every field inline.</p>
  </DocPage>
</template>
