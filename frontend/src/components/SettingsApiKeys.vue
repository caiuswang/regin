<script setup>
import { computed, onMounted, ref } from 'vue'
import api from '../api.js'
import Badge from './Badge.vue'
import Card from './Card.vue'
import Button from './ui/Button.vue'
import Icon from './ui/Icon.vue'
import Input from './ui/Input.vue'
import { KEY_SCOPES, useApiKeys } from '../composables/useApiKeys.js'
import { useCopy } from '../composables/useCopy.js'

const { keys, loading, saving, formError, minted, load, mint, revoke, dismissMinted } = useApiKeys()
const { copyText, isCopied } = useCopy()

const currentUser = ref(api.getStoredUser())
const isAdmin = computed(() => currentUser.value?.role === 'admin')
const newKeyName = ref('')

async function submit() {
  if (await mint(newKeyName.value)) newKeyName.value = ''
}

onMounted(load)
</script>

<template>
  <div class="space-y-5">
    <div class="sv-section-header">
      <h2 class="sv-section-title">API keys</h2>
      <p class="sv-section-desc">
        Machine credentials for <code>POST /api/tasks/capture</code>. A key is
        not a user: it can add backlog ideas and nothing else.
      </p>
    </div>

    <div v-if="!isAdmin" class="empty-state" data-testid="keys-forbidden">
      API keys are managed by admins.
    </div>

    <template v-else>
      <Card v-if="minted" data-testid="minted-key">
        <h2 class="card-header">Copy “{{ minted.name }}” now</h2>
        <p class="minted-warning" role="alert">
          This is the only time you will see this key. regin stores just its
          hash — if you lose it, mint another.
        </p>
        <div class="minted-row">
          <code class="minted-value">{{ minted.key }}</code>
          <Button variant="secondary" size="sm" aria-label="Copy API key"
                  data-testid="copy-key" @click="copyText(minted.key)">
            <Icon :name="isCopied(minted.key) ? 'check' : 'copy'" :size="13" />
            {{ isCopied(minted.key) ? 'Copied' : 'Copy' }}
          </Button>
          <Button variant="ghost" size="sm" @click="dismissMinted">Done</Button>
        </div>
      </Card>

      <Card>
        <h2 class="card-header">Mint a key</h2>
        <div class="max-w-sm">
          <label class="field-label" for="key-name">Name</label>
          <Input
            id="key-name"
            v-model="newKeyName"
            type="text"
            :error="!!formError"
            placeholder="phone shortcut"
            aria-label="API key name"
            @keyup.enter="submit"
          />
        </div>
        <p class="text-xs text-slate-500 mt-2">Scopes: {{ KEY_SCOPES.join(', ') }}</p>
        <p v-if="formError" class="minted-error" role="alert" data-testid="key-form-error">{{ formError }}</p>
        <Button variant="primary" class="mt-4" :disabled="saving || !newKeyName.trim()"
                data-testid="mint-key" @click="submit">
          Mint key
        </Button>
      </Card>

      <div v-if="loading" class="empty-state">Loading API keys…</div>
      <div v-else-if="!keys.length" class="empty-state" data-testid="keys-empty">
        No API keys yet.
      </div>
      <Card v-else :no-padding="true">
        <div class="overflow-x-auto">
          <table class="tbl" data-testid="keys-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Prefix</th>
                <th>Scopes</th>
                <th>Created</th>
                <th>Last used</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="key in keys" :key="key.id">
                <td class="font-medium">{{ key.name }}</td>
                <td class="font-mono text-xs">{{ key.key_prefix }}…</td>
                <td>
                  <Badge v-for="scope in key.scopes" :key="scope" color="blue" :label="scope" />
                </td>
                <td class="text-xs text-slate-500 font-mono">{{ key.created_at }}</td>
                <td class="text-xs text-slate-500 font-mono">{{ key.last_used_at || 'never' }}</td>
                <td>
                  <Badge v-if="key.revoked" color="gray" label="revoked" />
                  <Button v-else variant="danger" size="sm" :aria-label="`Revoke ${key.name}`"
                          @click="revoke(key)">
                    Revoke
                  </Button>
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
.minted-warning {
    font-size: 0.8125rem;
    color: var(--color-danger);
    margin-bottom: 0.75rem;
}
.minted-error {
    margin-top: 0.75rem;
    font-size: 0.8125rem;
    color: var(--color-danger);
}
.minted-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
}
.minted-value {
    flex: 1;
    min-width: 0;
    padding: 0.5rem 0.75rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface-2);
    font-size: 0.8125rem;
    color: var(--color-fg);
    overflow-wrap: anywhere;
}
</style>
