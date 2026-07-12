const TOKEN_KEY = 'regin_auth_token'
const USER_KEY = 'regin_auth_user'

function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token)
}

function clearAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

function getStoredUser() {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

function setStoredUser(user) {
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

function authHeaders() {
  const token = getToken()
  return token ? { 'Authorization': `Bearer ${token}` } : {}
}

// Centralized 401 handling: the token is gone or expired, so drop it and
// bounce to login. Guard against a redirect loop — the app shell stays
// mounted on /login and keeps polling (e.g. the schema-drift badge), so a
// 401 there must not reload the page.
function handleUnauthorized() {
  clearAuth()
  if (window.location.pathname !== '/login') {
    window.location.href = '/login'
  }
}

async function get(path) {
  const res = await fetch(`/api${path}`, {
    headers: authHeaders(),
  })
  if (res.status === 401) {
    handleUnauthorized()
    throw new Error('Session expired. Please log in again.')
  }
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

// Authenticated GET that returns an object URL the browser can hand to
// <img src>. Binary endpoints (e.g. prompt-image bytes) can't reuse
// `get()` because <img> tags can't attach the Authorization header on
// their own. The caller owns the returned URL — revoke it with
// URL.revokeObjectURL on unmount to avoid leaking blob references.
async function getBlobUrl(path) {
  const res = await fetch(`/api${path}`, { headers: authHeaders() })
  if (res.status === 401) {
    handleUnauthorized()
    throw new Error('Session expired. Please log in again.')
  }
  if (!res.ok) throw new Error(await res.text())
  return URL.createObjectURL(await res.blob())
}

async function post(path, body) {
  return _mutate('POST', path, body)
}

async function patch(path, body) {
  return _mutate('PATCH', path, body)
}

async function put(path, body) {
  return _mutate('PUT', path, body)
}

async function del(path, body) {
  return _mutate('DELETE', path, body)
}

async function _mutate(method, path, body) {
  const headers = { ...authHeaders() }
  if (body != null) headers['Content-Type'] = 'application/json'

  const res = await fetch(`/api${path}`, {
    method,
    headers,
    body: body != null ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401) {
    handleUnauthorized()
    return { ok: false, msg: 'Session expired. Please log in again.' }
  }
  if (!res.ok) {
    const text = await res.text()
    let msg, detail
    try {
      const parsed = JSON.parse(text)
      msg = parsed.error || parsed.msg
      detail = parsed.detail
    } catch { msg = text }
    return { ok: false, msg: msg || `Server error (${res.status})`, detail }
  }
  return res.json()
}

async function login(username, password) {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    return { ok: false, msg: data.error || 'Login failed' }
  }
  const data = await res.json()
  setToken(data.token)
  setStoredUser(data.user)
  return { ok: true, user: data.user }
}

// `role` is honoured only for admin-created users (an authed admin calling
// this post-bootstrap); the bootstrap first-run call sends no token and no
// role. authHeaders() is empty when no token is stored, so the setup flow is
// unaffected — but once logged in, the admin's JWT rides along so the backend
// can authorize the create.
async function register(username, displayName, password, email, role) {
  const res = await fetch('/api/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ username, display_name: displayName, password, email, role }),
  })
  const data = await res.json()
  if (!res.ok) return { ok: false, msg: data.error || 'Registration failed' }
  return { ok: true, ...data }
}

function logout() {
  clearAuth()
  window.location.href = '/login'
}

async function checkAuth() {
  const res = await fetch('/api/auth/me', { headers: authHeaders() })
  const data = await res.json()
  if (data.user) {
    setStoredUser(data.user)
    return { user: data.user, needsSetup: false, mode: data.mode }
  }
  return { user: null, needsSetup: data.needs_setup, mode: data.mode }
}

export default {
  get, post, put, patch, del, delete: del, getBlobUrl,
  login, register, logout, checkAuth,
  getToken, getStoredUser, clearAuth,
}
