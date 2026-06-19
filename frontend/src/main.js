import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './assets/style.css'
import PrimeVue from 'primevue/config'
import Aura from '@primeuix/themes/aura'

const app = createApp(App)
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
