# regin website

The official project site, live at **[regin.ccday.top](https://regin.ccday.top)** — a
standalone Vue 3 + Vite app that introduces regin — a workbench for building software with AI coding agents — to prospective users. It is deliberately kept **separate from the operator dashboard** in
[`frontend/`](../frontend): different audience, different cadence, and its own minimal
dependency set (just `vue` + `vue-router`). It shares no build, no `node_modules`, and no
code with the rest of the repo — it lives here as a plain subdirectory, not a submodule.

## Develop

```bash
cd website
npm install
npm run dev                          # Vite dev server on http://localhost:5173

# Expose on your LAN / bind a specific host or port:
npm run dev -- --host               # listen on all interfaces (0.0.0.0)
npm run dev -- --host 0.0.0.0 --port 8080
```

`npm run preview` accepts the same `--host` / `--port` flags for serving the
production build.

## Build

```bash
npm run build      # → website/dist/ (static assets, gitignored)
npm run preview    # serve the production build locally
```

## Deploy

The live site at **[regin.ccday.top](https://regin.ccday.top)** deploys automatically:
[`.github/workflows/deploy-website.yml`](../.github/workflows/deploy-website.yml) runs
`npm ci && npm run build` and rsyncs `website/dist/` to the server on every push to
`master` that touches `website/**` (also runnable via **workflow_dispatch**). Deploy
credentials come from the `REGIN_SERVER_HOST` / `REGIN_SERVER_USER` /
`REGIN_SSH_PRIVATE_KEY` repo secrets.

To host it elsewhere: `npm run build` emits a fully static bundle to `website/dist/` with
zero third-party runtime requests (fonts are self-hosted). Point any static host at this
subdirectory:

- **Build command:** `npm run build`
- **Publish directory:** `website/dist`

No backend is required — the site does not talk to the regin Flask API.

## Content

Page copy lives in [`src/content/`](src/content), separated from the components that render it:

| File | Feeds |
|---|---|
| `features.js` | the home page: `LOOP` (Spec → Run → Watch → Count), `LAYERS` (Workbench, Runs, Observability, Guidance — in that order) and `PILLARS`, one real artifact per layer |
| `cli.js` | `/cli`: core commands, `COMMAND_GROUPS` (every group `regin --help` lists), troubleshooting |
| `settings.js` | `/configuration` |
| `topics.js` | `/topics` and its sub-pages under Guidance |
| `columns.js` | shared table column definitions |

Pages: `/` (Introduction), `/getting-started`, `/pipeline`, `/runs`, `/dashboard`, `/guidance`
(with `/topics/*` beneath it), `/architecture`, `/configuration`, `/cli`.

These files restate regin concepts, so **update them alongside the corresponding product
change** — otherwise the site drifts from what regin actually does. Every artifact on the
home page is real output with its feature or trace id and date named; don't replace one
with an invented example.
