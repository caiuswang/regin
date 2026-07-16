<script setup>
import DocPage from '../components/DocPage.vue'
import CodeBlock from '../components/CodeBlock.vue'
import Callout from '../components/Callout.vue'
import DataTable from '../components/DataTable.vue'
import { CORE_COMMANDS, COMMAND_GROUPS, TROUBLESHOOTING } from '../content/cli.js'

const TOC = [
  { id: 'invoking', label: 'Invoking the CLI' },
  { id: 'core-commands', label: 'Core commands' },
  { id: 'command-groups', label: 'Grouped subcommands' },
  { id: 'troubleshooting', label: 'Troubleshooting' },
]

const CMD_COLUMNS = [
  { key: 'cmd', label: 'Command', code: true },
  { key: 'desc', label: 'What it does' },
]
const GROUP_COLUMNS = [
  { key: 'group', label: 'Group', code: true },
  { key: 'sub', label: 'Subcommands' },
  { key: 'desc', label: 'Purpose' },
]
const FIX_COLUMNS = [
  { key: 'symptom', label: 'Symptom' },
  { key: 'fix', label: 'Fix' },
]
</script>

<template>
  <DocPage
    title="CLI Reference"
    lead="Every operation the web dashboard offers — and the maintenance ones it doesn't — is available from the command line."
    :toc="TOC"
  >
    <h2 id="invoking">Invoking the CLI</h2>
    <p>Run commands from the repo root through the project venv — the system interpreter lacks regin's dependencies:</p>
    <CodeBlock :code="'.venv/bin/python cli/regin.py <command>\n\n# examples\n.venv/bin/python cli/regin.py doctor\n.venv/bin/python cli/regin.py serve'" />
    <Callout tone="info">
      Command listings below use the short <code>regin</code> form for readability;
      substitute <code>.venv/bin/python cli/regin.py</code> unless you've installed an alias.
    </Callout>

    <h2 id="core-commands">Core commands</h2>
    <DataTable :columns="CMD_COLUMNS" :rows="CORE_COMMANDS" />

    <h2 id="command-groups">Grouped subcommands</h2>
    <p>Run any group with <code>--help</code> for the full list and flags.</p>
    <DataTable :columns="GROUP_COLUMNS" :rows="COMMAND_GROUPS" />

    <h2 id="troubleshooting">Troubleshooting</h2>
    <DataTable :columns="FIX_COLUMNS" :rows="TROUBLESHOOTING" />
  </DocPage>
</template>
