/**
 * The API origin for specs that bypass the vite proxy and call the server
 * directly. Set by `playwright.config.js` (via `e2e-env.js`) to the suite's own
 * server; the literal fallback only applies when a spec file is executed
 * outside Playwright.
 *
 * Hardcoding `:8321` here is what let a spec reach past its own server and
 * mutate the dev stack's database — `import-*.spec.js` deletes patterns and
 * `auth-fixture.js` logs in against the real `users` table.
 */
export const API_BASE = process.env.REGIN_E2E_API_BASE || 'http://localhost:8321'
