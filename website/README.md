# regin website

The official project site — a standalone Vue 3 + Vite app that introduces regin to
prospective users. It is deliberately kept **separate from the operator dashboard** in
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

`npm run build` emits a fully static bundle to `website/dist/` with zero third-party
runtime requests (fonts are self-hosted). Point any static host at this subdirectory:

- **Build command:** `npm run build`
- **Publish directory:** `website/dist`

No backend is required — the site does not talk to the regin Flask API.

## Content

Page copy lives in [`src/content/`](src/content) (`features.js`, `settings.js`,
`topics.js`, `cli.js`, …), separated from the components that render it. These files
restate regin concepts, so **update them alongside the corresponding product change** —
otherwise the site drifts from what regin actually does.
