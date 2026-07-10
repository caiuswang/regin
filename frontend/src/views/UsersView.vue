<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../api.js'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'
import Button from '../components/ui/Button.vue'
import Select from '../components/ui/Select.vue'
import Input from '../components/ui/Input.vue'
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

const ROLE_OPTIONS = ['admin', 'editor', 'viewer']

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
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 max-w-lg">
        <div>
          <label class="field-label">Username</label>
          <Input :model-value="currentUser?.username" disabled aria-label="Username" class="is-readonly font-mono" />
        </div>
        <div>
          <label class="field-label">Role</label>
          <Input :model-value="currentUser?.role" disabled aria-label="Role" class="is-readonly" />
        </div>
        <div>
          <label class="field-label">Display name</label>
          <Input v-model="displayName" type="text" aria-label="Display name" />
        </div>
        <div>
          <label class="field-label">Email</label>
          <Input v-model="email" type="email" aria-label="Email" />
        </div>
      </div>
      <Button variant="primary" class="mt-4" @click="saveProfile">
        Save profile
      </Button>
    </Card>

    <!-- Change Password -->
    <Card>
      <h2 class="card-header">Change password</h2>
      <div class="max-w-sm space-y-3">
        <div>
          <label class="field-label">Current password</label>
          <Input v-model="oldPassword" type="password" aria-label="Current password" />
        </div>
        <div>
          <label class="field-label">New password</label>
          <Input v-model="newPassword" type="password" aria-label="New password" />
        </div>
        <div>
          <label class="field-label">Confirm new password</label>
          <Input v-model="confirmPassword" type="password" aria-label="Confirm new password" />
        </div>
      </div>
      <Button variant="primary" class="mt-4" @click="changePassword">
        Change password
      </Button>
    </Card>

    <!-- User Management (admin only) -->
    <template v-if="isAdmin">
      <h2 class="section-heading">Team members</h2>
      <Card :no-padding="true">
        <div class="overflow-x-auto">
        <table class="tbl users-tbl">
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
                  <span class="role-select-wrap">
                    <Select :model-value="u.role" :options="ROLE_OPTIONS" block
                      :aria-label="`Role for ${u.username}`"
                      @change="setRole(u.id, $event.target.value); $event.target.value = u.role" />
                  </span>
                  <Button variant="danger" size="sm" @click="deleteUser(u.id, u.username)">
                    Delete
                  </Button>
                </div>
                <span v-else class="text-xs text-slate-400">you</span>
              </td>
            </tr>
          </tbody>
        </table>
        </div>
      </Card>
    </template>
  </div>
</template>

<style scoped>
.role-select-wrap {
    display: inline-block;
    width: 7rem;
    font-size: 0.75rem;
}
/* Keep the identity column visible while the rest of the row scrolls,
   so role changes / deletes always show who they apply to. */
.users-tbl th:first-child,
.users-tbl td:first-child {
    position: sticky;
    left: 0;
    z-index: 1;
    background: var(--color-white);
    box-shadow: inset -1px 0 0 var(--color-slate-100);
}
.users-tbl thead th:first-child { background: var(--color-slate-50); }
.users-tbl tbody tr:hover td:first-child { background: var(--color-slate-50); }
</style>
