<script setup>
import DocPage from '../components/DocPage.vue'
import CodeBlock from '../components/CodeBlock.vue'
import Callout from '../components/Callout.vue'
import DataTable from '../components/DataTable.vue'
import { STATE_LAYERS } from '../content/settings.js'

const TOC = [
  { id: 'quick-start', label: 'Quick start' },
  { id: 'manual-setup', label: 'Manual setup' },
  { id: 'server-modes', label: 'Server modes' },
  { id: 'accounts', label: 'Accounts & roles' },
  { id: 'state-layers', label: 'Shared vs local state' },
]

const ROLE_COLUMNS = [
  { key: 'role', label: 'Role', code: true },
  { key: 'view', label: 'View' },
  { key: 'edit', label: 'Edit patterns / skills / rules / settings' },
  { key: 'manage', label: 'Manage users' },
]
const ROLE_ROWS = [
  { role: 'admin', view: 'all', edit: 'all', manage: 'yes' },
  { role: 'editor', view: 'all', edit: 'all', manage: 'no' },
  { role: 'viewer', view: 'all', edit: 'own profile only', manage: 'no' },
]

const STATE_COLUMNS = [
  { key: 'layer', label: 'Layer' },
  { key: 'what', label: 'What' },
  { key: 'where', label: 'Where', code: true },
]
</script>

<template>
  <DocPage
    title="Getting Started"
    lead="Install, configure, and run a regin instance. regin is in early beta — schemas, settings keys, hook contracts, and CLI flags may change without shims; pin a commit if you need stability."
    :toc="TOC"
  >
    <h2 id="quick-start">Quick start</h2>
    <p>The setup script creates a Python venv, builds the frontend, initializes the database, discovers repos, and prompts you to create your user account.</p>
    <CodeBlock :code="'git clone <repo-url> regin\ncd regin\n./scripts/setup.sh /path/to/your/source-repos\n\n.venv/bin/python cli/regin.py serve\n# Open http://localhost:8321 and log in'" />

    <h2 id="manual-setup">Manual setup</h2>
    <h3>1. Install dependencies</h3>
    <CodeBlock :code="'python3 -m venv .venv\n.venv/bin/pip install -e .\ncd frontend && npm install && npx vite build && cd ..'" />
    <h3>2. Initialize the database</h3>
    <CodeBlock :code="'.venv/bin/python cli/regin.py init --force\n.venv/bin/python cli/regin.py rebuild'" />
    <p><code>init --force</code> is a true local reset: it recreates the SQLite DB from scratch, removes tracked deployed skill directories, clears local experiments, tags, accounts, and deployment records, and rotates the JWT secret so old login tokens stop working.</p>
    <h3>3. Configure local settings</h3>
    <p>Create <code>config/settings.local.json</code> (machine-local, gitignored):</p>
    <CodeBlock :code="'{\n  &quot;mode&quot;: &quot;standalone&quot;,\n  &quot;active_provider&quot;: &quot;claude&quot;\n}'" />
    <p>Register repos through the <code>/repos</code> page in the web UI, or from the CLI with <code>regin add-repo /path/to/your/source-repo</code>. Registered paths persist in <code>settings.repo_paths</code>.</p>
    <h3>4. Create your account and serve</h3>
    <CodeBlock :code="'.venv/bin/python cli/regin.py users init-db\n.venv/bin/python cli/regin.py users create <username> <password> --display-name &quot;Your Name&quot;\n.venv/bin/python cli/regin.py serve'" />
    <Callout tone="warn">
      <strong>Don't expose <code>regin serve</code> to the network until the first user exists.</strong>
      The dashboard binds to 127.0.0.1 by default. The first user to register inherits the admin
      role, and there is no separate “open registration” toggle — if you expose the port before
      any account exists, anyone who can reach the URL can become admin.
    </Callout>

    <h2 id="server-modes">Server modes</h2>
    <p><strong>Standalone</strong> (default) keeps everything in local SQLite — single user, no MySQL needed. <strong>Shared</strong> is team mode: user accounts and audit logs move to a MySQL instance every team member connects to.</p>
    <CodeBlock :code="'{\n  &quot;mode&quot;: &quot;shared&quot;,\n  &quot;database_url&quot;: &quot;mysql://user:password@host:3306/regin&quot;\n}'" />
    <p>Switching modes deletes nothing: patterns, repos, tags, experiments, and rule triggers always live in the local SQLite database. Only accounts and audit logs live in the mode-dependent database, so you may need to re-create your admin user after a switch.</p>

    <h2 id="accounts">Accounts &amp; roles</h2>
    <p>The first user created gets the <code>admin</code> role automatically. Admins can manage roles from the web UI at <code>/account</code> or via <code>regin users</code>.</p>
    <DataTable :columns="ROLE_COLUMNS" :rows="ROLE_ROWS" />

    <h2 id="state-layers">Shared vs local state</h2>
    <p>Each machine runs its own local instance. Patterns, rule-engine sources, and tag definitions are user-generated data and live in your XDG data directory, not the repo. Relocate the whole tree at once with <code>REGIN_DATA_DIR</code>.</p>
    <DataTable :columns="STATE_COLUMNS" :rows="STATE_LAYERS" />
    <p>Next: tune the instance on the <RouterLink to="/configuration">Configuration</RouterLink> page, or see the full <RouterLink to="/cli">CLI reference</RouterLink>.</p>
  </DocPage>
</template>
