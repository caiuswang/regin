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
