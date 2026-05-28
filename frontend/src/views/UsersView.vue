<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../api.js'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'
import { useFlash } from '../composables/useFlash.js'
import { useConfirm } from '../composables/useConfirm.js'

const { flash } = useFlash()
const { confirm } = useConfirm()
const currentUser = ref(api.getStoredUser())
const users = ref([])
const loading = ref(true)
const isAdmin = computed(() => currentUser.value?.role === 'admin')

const displayName = ref('')
const email = ref('')

const oldPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')

onMounted(async () => {
  const meData = await api.get('/auth/me')
  if (meData.user) currentUser.value = meData.user

  const profile = await loadProfile()
  displayName.value = profile?.display_name || ''
  email.value = profile?.email || ''

  if (isAdmin.value) {
    users.value = await api.get('/users')
  }
  loading.value = false
})

async function loadProfile() {
  const all = await api.get('/users').catch(() => [])
  return all.find(u => u.id === currentUser.value?.id)
}

async function saveProfile() {
  const result = await api.post('/auth/profile', {
    display_name: displayName.value,
    email: email.value,
  })
  if (result.ok) {
    flash('Profile updated')
    const stored = api.getStoredUser()
    if (stored) {
      stored.display_name = displayName.value
      localStorage.setItem('regin_auth_user', JSON.stringify(stored))
      currentUser.value = stored
    }
  } else {
    flash(result.msg || 'Failed', 'error')
  }
}

async function changePassword() {
  if (newPassword.value !== confirmPassword.value) {
    flash('Passwords do not match', 'error')
    return
  }
  if (newPassword.value.length < 4) {
    flash('Password must be at least 4 characters', 'error')
    return
  }
  const result = await api.post('/auth/change-password', {
    old_password: oldPassword.value,
    new_password: newPassword.value,
  })
  if (result.ok) {
    flash('Password changed')
    oldPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
  } else {
    flash(result.msg || result.error || 'Failed', 'error')
  }
}

async function setRole(userId, role) {
  const result = await api.post(`/users/${userId}/role`, { role })
  if (result.ok) {
    flash(result.msg)
    users.value = await api.get('/users')
  } else {
    flash(result.msg || result.error || 'Failed', 'error')
  }
}

async function deleteUser(userId, username) {
  const ok = await confirm('Delete user', `Delete user "${username}"? This cannot be undone.`, true)
  if (!ok) return
  const result = await api.post(`/users/${userId}/delete`)
  if (result.ok) {
    flash(result.msg)
    users.value = await api.get('/users')
  } else {
    flash(result.msg || result.error || 'Failed', 'error')
  }
}

const roleBadgeColor = (role) => {
  if (role === 'admin') return 'red'
  if (role === 'editor') return 'blue'
  return 'gray'
}
</script>

<template>
  <div v-if="loading" class="empty-state">Loading account…</div>
  <div v-else>
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">System</div>
        <h1 class="page-title">Account</h1>
        <p class="page-subtitle">Manage your profile and team members.</p>
      </div>
    </header>

    <!-- Profile -->
    <Card>
      <h2 class="card-header">Profile</h2>
      <div class="grid grid-cols-2 gap-4 max-w-lg">
        <div>
          <label class="field-label">Username</label>
          <input type="text" :value="currentUser?.username" disabled aria-label="Username" class="input is-readonly font-mono">
        </div>
        <div>
          <label class="field-label">Role</label>
          <input type="text" :value="currentUser?.role" disabled aria-label="Role" class="input is-readonly">
        </div>
        <div>
          <label class="field-label">Display name</label>
          <input v-model="displayName" type="text" aria-label="Display name"
            class="input focus-visible:outline-2 focus-visible:outline-blue-500">
        </div>
        <div>
          <label class="field-label">Email</label>
          <input v-model="email" type="email" aria-label="Email"
            class="input focus-visible:outline-2 focus-visible:outline-blue-500">
        </div>
      </div>
      <button type="button" @click="saveProfile"
        class="btn btn-primary mt-4 focus-visible:outline-2 focus-visible:outline-blue-500">
        Save profile
      </button>
    </Card>

    <!-- Change Password -->
    <Card>
      <h2 class="card-header">Change password</h2>
      <div class="max-w-sm space-y-3">
        <div>
          <label class="field-label">Current password</label>
          <input v-model="oldPassword" type="password" aria-label="Current password"
            class="input focus-visible:outline-2 focus-visible:outline-blue-500">
        </div>
        <div>
          <label class="field-label">New password</label>
          <input v-model="newPassword" type="password" aria-label="New password"
            class="input focus-visible:outline-2 focus-visible:outline-blue-500">
        </div>
        <div>
          <label class="field-label">Confirm new password</label>
          <input v-model="confirmPassword" type="password" aria-label="Confirm new password"
            class="input focus-visible:outline-2 focus-visible:outline-blue-500">
        </div>
      </div>
      <button type="button" @click="changePassword"
        class="btn btn-primary mt-4 focus-visible:outline-2 focus-visible:outline-blue-500">
        Change password
      </button>
    </Card>

    <!-- User Management (admin only) -->
    <template v-if="isAdmin">
      <h2 class="section-heading">Team members</h2>
      <Card :no-padding="true">
        <table class="tbl">
          <thead>
            <tr>
              <th>Username</th>
              <th>Display name</th>
              <th>Email</th>
              <th>Role</th>
              <th>Last login</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in users" :key="u.id">
              <td class="font-medium">{{ u.username }}</td>
              <td>{{ u.display_name }}</td>
              <td class="text-slate-500">{{ u.email || '-' }}</td>
              <td><Badge :color="roleBadgeColor(u.role)" :label="u.role" /></td>
              <td class="text-xs text-slate-500 font-mono">{{ u.last_login || 'never' }}</td>
              <td>
                <div v-if="u.id !== currentUser?.id" class="flex gap-2 items-center">
                  <select @change="setRole(u.id, $event.target.value); $event.target.value = u.role"
                    :value="u.role"
                    :aria-label="`Role for ${u.username}`"
                    class="input role-select focus-visible:outline-2 focus-visible:outline-blue-500">
                    <option value="admin">admin</option>
                    <option value="editor">editor</option>
                    <option value="viewer">viewer</option>
                  </select>
                  <button type="button" @click="deleteUser(u.id, u.username)"
                    class="btn btn-danger text-xs focus-visible:outline-2 focus-visible:outline-blue-500">
                    Delete
                  </button>
                </div>
                <span v-else class="text-xs text-slate-400">you</span>
              </td>
            </tr>
          </tbody>
        </table>
      </Card>
    </template>
  </div>
</template>

<style scoped>
.role-select {
    width: auto;
    padding-right: 1.5rem;
    font-size: 0.75rem;
}
</style>
