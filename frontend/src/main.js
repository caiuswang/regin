import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './assets/style.css'
import PrimeVue from 'primevue/config'
import Aura from '@primeuix/themes/aura'

const app = createApp(App)

// Element→source stamping plus the Ctrl+Shift+G grab overlay. Dynamic import
// keeps the whole thing out of production bundles. See src/dev/.
// Awaited, not fire-and-forget: the stamping mixin must be registered before
// app.mount() below, or every already-mounted component goes unstamped.
if (import.meta.env.DEV) {
  const dev = await import('./dev')
  dev.installDevTools(app)
}

app.use(router)
app.use(PrimeVue, {
  theme: {
    preset: Aura,
    options: {
      // Match the same switch the rest of the app uses (data-theme on <html>),
      // so PrimeVue components flip in lockstep with useTheme.js.
      darkModeSelector: '[data-theme="dark"]',
    },
  },
})
app.mount('#app')
