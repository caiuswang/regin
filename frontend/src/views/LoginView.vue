<script setup>
import { ref, onMounted, computed, useTemplateRef } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api.js'

const router = useRouter()
const mode = ref('login') // 'login' | 'register' | 'setup'
const username = ref('')
const displayName = ref('')
const password = ref('')
const email = ref('')
const error = ref('')
const loading = ref(false)
const usernameInput = useTemplateRef('usernameInput')

onMounted(async () => {
  const { user, needsSetup } = await api.checkAuth()
  if (user) {
    router.replace('/')
    return
  }
  if (needsSetup) {
    mode.value = 'setup'
  }
  usernameInput.value?.focus()
})

async function handleLogin() {
  error.value = ''
  loading.value = true
  const result = await api.login(username.value, password.value)
  loading.value = false
  if (!result.ok) {
    error.value = result.msg
    return
  }
  router.replace('/')
}

async function handleRegister() {
  error.value = ''
  if (password.value.length < 4) {
    error.value = 'Password must be at least 4 characters'
    return
  }
  loading.value = true
  const result = await api.register(
    username.value,
    displayName.value || username.value,
    password.value,
    email.value || undefined,
  )
  loading.value = false
  if (!result.ok) {
    error.value = result.msg
    return
  }
  const loginResult = await api.login(username.value, password.value)
  if (loginResult.ok) router.replace('/')
}

const submitLabel = computed(() => {
  if (loading.value) return '…'
  if (mode.value === 'login') return 'Sign in'
  if (mode.value === 'setup') return 'Create admin account'
  return 'Register'
})

const headline = computed(() => {
  if (mode.value === 'setup') return 'Create admin account'
  if (mode.value === 'register') return 'Register'
  return 'Sign in'
})
</script>

<template>
  <div class="login-shell">
    <div class="login-card">
      <div class="login-brand">
        <div class="login-brand-mark">r</div>
        <div>
          <div class="login-brand-name">regin</div>
          <div class="login-brand-meta">Pattern reference for AI agents</div>
        </div>
      </div>

      <h1 class="login-title">{{ headline }}</h1>

      <div v-if="error" class="login-error" role="alert">
        {{ error }}
      </div>

      <form @submit.prevent="mode === 'login' ? handleLogin() : handleRegister()">
        <div class="mb-3">
          <label class="field-label">Username</label>
          <input v-model="username" ref="usernameInput" type="text" required aria-label="Username"
            class="input focus-visible:outline-2 focus-visible:outline-blue-500">
        </div>

        <div v-if="mode !== 'login'" class="mb-3">
          <label class="field-label">Display name</label>
          <input v-model="displayName" type="text" :placeholder="username" aria-label="Display name"
            class="input focus-visible:outline-2 focus-visible:outline-blue-500">
        </div>

        <div v-if="mode !== 'login'" class="mb-3">
          <label class="field-label">Email <span class="text-slate-400 font-normal">(optional)</span></label>
          <input v-model="email" type="email" aria-label="Email (optional)"
            class="input focus-visible:outline-2 focus-visible:outline-blue-500">
        </div>

        <div class="mb-4">
          <label class="field-label">Password</label>
          <input v-model="password" type="password" required aria-label="Password"
            class="input focus-visible:outline-2 focus-visible:outline-blue-500">
        </div>

        <button type="submit" :disabled="loading"
          class="btn btn-primary login-submit focus-visible:outline-2 focus-visible:outline-blue-500">
          {{ submitLabel }}
        </button>
      </form>

      <div v-if="mode === 'login'" class="login-switch">
        <button type="button" @click="mode = 'register'"
          class="text-link focus-visible:outline-2 focus-visible:outline-blue-500">
          Create an account
        </button>
      </div>
      <div v-if="mode === 'register'" class="login-switch">
        <button type="button" @click="mode = 'login'"
          class="text-link focus-visible:outline-2 focus-visible:outline-blue-500">
          Back to login
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-shell {
    min-height: 60vh;
    display: flex;
    align-items: center;
    justify-content: center;
}

.login-card {
    background: #fff;
    border-radius: 1.125rem;
    border: 1px solid #F1F5F9;
    box-shadow: 0 8px 32px rgba(15, 23, 42, 0.08);
    padding: 2rem 1.75rem;
    width: 100%;
    max-width: 24rem;
}

.login-brand {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 1.5rem;
}

.login-brand-mark {
    width: 2.5rem;
    height: 2.5rem;
    border-radius: 0.75rem;
    background: linear-gradient(135deg, #1E40AF, #3B82F6);
    color: #fff;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 700;
    font-size: 1.125rem;
    box-shadow: 0 4px 12px rgba(30, 64, 175, 0.25);
}

.login-brand-name {
    font-size: 1rem;
    font-weight: 700;
    color: #0F172A;
    line-height: 1.1;
}

.login-brand-meta {
    font-size: 0.6875rem;
    color: #94A3B8;
    margin-top: 2px;
}

.login-title {
    font-size: 1.5rem;
    font-weight: 700;
    color: #0F172A;
    margin-bottom: 1.5rem;
    line-height: 1.2;
}

.login-error {
    background: #FEF2F2;
    color: #B91C1C;
    border-radius: 0.5rem;
    padding: 0.5rem 0.75rem;
    font-size: 0.8125rem;
    margin-bottom: 1rem;
}

.login-submit {
    width: 100%;
    justify-content: center;
    padding: 0.625rem 1rem;
}

.login-switch {
    margin-top: 1rem;
    text-align: center;
    font-size: 0.8125rem;
}

.login-switch .text-link {
    background: transparent;
    border: 0;
    cursor: pointer;
    padding: 0.25rem 0.5rem;
    border-radius: 4px;
}
</style>
