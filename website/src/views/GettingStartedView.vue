<script setup>
import DocPage from '../components/DocPage.vue'
import CodeBlock from '../components/CodeBlock.vue'
import Callout from '../components/Callout.vue'
import DataTable from '../components/DataTable.vue'
import { STATE_LAYERS } from '../content/settings.js'
import installShot from '../assets/shots/install-dark.png'
import installShotWebp from '../assets/shots/install-dark.webp'

const TOC = [
  { id: 'quick-start', label: 'Quick start' },
  { id: 'activate-hooks', label: 'Activate the hooks' },
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

    <h2 id="activate-hooks">Activate the hooks</h2>
    <Callout tone="warn">
      <strong>Serving the dashboard is not enough on its own.</strong> Neither
      <code>setup.sh</code> nor <code>regin serve</code> wires regin into your agent —
      until you install the hooks, no session traces are captured, no lint or rewrite
      rules fire, no agent memory is injected, and <code>send_to_user</code> messages go
      nowhere. The dashboard will just sit empty.
    </Callout>
    <p>regin drives every Claude Code event through one dispatcher — <code>python -m hook_manager &lt;Event&gt;</code> — that has to be registered in the provider's settings file (<code>~/.claude/settings.json</code> for Claude). Install it once from the running dashboard, under <strong>Settings → Hook Installers</strong>:</p>
    <figure class="shot-frame shot-frame-sm">
      <picture>
        <source type="image/webp" :srcset="installShotWebp" width="2720" height="1380" />
        <img
          :src="installShot" width="2720" height="1380" loading="lazy"
          alt="The regin Settings page, Hook Installers section: a Claude Code card showing the settings.json path, a 'hooks supported' badge, and the recommended Hook Manager dispatcher with an install control."
        />
      </picture>
      <figcaption>Settings → Hook Installers → install <strong>Hook Manager</strong> for Claude Code. It writes the dispatcher into the <code>settings.json</code> shown on the card; the optional Debug Hook only logs raw payloads.</figcaption>
    </figure>
    <p>Installing writes the per-event <code>hooks</code> block into your Claude settings; the <em>Hook Handlers</em> section of the same page then toggles individual handlers on or off. Prefer to wire it by hand? Merge <code>hook_manager/settings.example.json</code> into <code>~/.claude/settings.json</code>, or preview the exact diff first with <code>.venv/bin/python -m hook_manager.migration_preview</code>. New Claude sessions started after the hooks land pick them up automatically.</p>

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
    <p>Once that admin account exists, expose the dashboard to other machines by binding all interfaces:</p>
    <CodeBlock :code="'.venv/bin/python cli/regin.py serve --host 0.0.0.0 --port 8321\n# reachable at http://&lt;this-machine-ip&gt;:8321'" />
    <p><code>--host</code> defaults to <code>127.0.0.1</code> (localhost only); <code>0.0.0.0</code> binds every interface. For anything past a trusted LAN, front it with a reverse proxy — <code>regin serve</code> runs Flask's development server.</p>
    <h3>5. Wire the hooks into your agent</h3>
    <p>Serving the dashboard does not connect regin to Claude Code. Finish with the <RouterLink to="#activate-hooks">Activate the hooks</RouterLink> step above — install the <code>hook_manager</code> dispatcher — or nothing gets captured or enforced.</p>

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
    <p>Next: take the <RouterLink to="/dashboard">dashboard tour</RouterLink> to see what the surfaces do, tune the instance on the <RouterLink to="/configuration">Configuration</RouterLink> page, or see the full <RouterLink to="/cli">CLI reference</RouterLink>.</p>
  </DocPage>
</template>
