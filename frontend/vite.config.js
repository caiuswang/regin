import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/postcss'
import autoprefixer from 'autoprefixer'

export default defineConfig({
  plugins: [vue()],
  css: {
    postcss: {
      plugins: [tailwindcss(), autoprefixer()],
    },
  },
  server: {
    port: 5173,
    // Wildcard binding covers both loopback families, so it cannot matter whether a
    // browser resolves `localhost` to ::1 or 127.0.0.1. Binding one family instead
    // lets a second `vite` take the other and coexist silently on the same port —
    // the HMR socket then lands on whichever server did not serve the page, which
    // reads as "broken on some browsers only". strictPort makes that second instance
    // fail loudly instead.
    host: true,
    strictPort: true,
    // A dead HMR socket leaves the client's `ws` undefined, but forwardConsole still
    // routes window.onerror/onunhandledrejection through it. The resulting TypeError
    // is itself an unhandled rejection, which re-enters the same handler: an infinite
    // console loop. Dropping that one leg needs logLevels restated too — `enabled` is
    // `unhandledErrors || logLevels.length > 0`, so omitting them turns forwarding off
    // wholesale and the terminal stops seeing console output at all.
    forwardConsole: { unhandledErrors: false, logLevels: ['error', 'warn'] },
    proxy: {
      '/api': {
        target: 'http://localhost:8321',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: '../web/static/dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
  },
})
