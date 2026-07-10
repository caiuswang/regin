<script setup>
import { ref, onMounted, computed, useTemplateRef } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api.js'
import Button from '../components/ui/Button.vue'
import Input from '../components/ui/Input.vue'

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
          <Input v-model="username" ref="usernameInput" type="text" required aria-label="Username" />
        </div>

        <div v-if="mode !== 'login'" class="mb-3">
          <label class="field-label">Display name</label>
          <Input v-model="displayName" type="text" :placeholder="username" aria-label="Display name" />
        </div>

        <div v-if="mode !== 'login'" class="mb-3">
          <label class="field-label">Email <span class="text-slate-400 font-normal">(optional)</span></label>
          <Input v-model="email" type="email" aria-label="Email (optional)" />
        </div>

        <div class="mb-4">
          <label class="field-label">Password</label>
          <Input v-model="password" type="password" required aria-label="Password" />
        </div>

        <Button type="submit" variant="primary" :disabled="loading" class="login-submit">
          {{ submitLabel }}
        </Button>
      </form>

      <div v-if="mode === 'login'" class="login-switch">
        <Button variant="link" class="min-h-9 px-3" @click="mode = 'register'">
          Create an account
        </Button>
      </div>
      <div v-if="mode === 'register'" class="login-switch">
        <Button variant="link" class="min-h-9 px-3" @click="mode = 'login'">
          Back to login
        </Button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-shell {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1rem;
    background: linear-gradient(180deg, var(--color-slate-50) 0%, var(--color-slate-100) 100%);
}

.login-card {
    background: var(--color-white);
    border-radius: 1.125rem;
    border: 1px solid var(--color-slate-100);
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
    background: linear-gradient(135deg, var(--color-blue-800), var(--color-blue-500));
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
    color: var(--color-slate-900);
    line-height: 1.1;
}

.login-brand-meta {
    font-size: 0.6875rem;
    color: var(--color-slate-400);
    margin-top: 2px;
}

.login-title {
    font-size: 1.5rem;
    font-weight: 700;
    color: var(--color-slate-900);
    margin-bottom: 1.5rem;
    line-height: 1.2;
}

.login-error {
    background: var(--color-red-50);
    color: var(--color-red-700);
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
