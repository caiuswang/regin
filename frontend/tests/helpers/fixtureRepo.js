/**
 * Write a throwaway git repo under the suite's scratch dir and register it
 * through POST /api/repos, returning its project id.
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync } from 'node:fs'
import { join } from 'node:path'
import { expect } from '@playwright/test'
import { SCRATCH_DIR, TEST_USER } from '../../e2e-env.js'
import { API_BASE } from './api-base.js'

export async function registerFixtureRepo(playwright, prefix, populate) {
  mkdirSync(SCRATCH_DIR, { recursive: true })
  const ctx = await playwright.request.newContext()
  try {
    const login = await ctx.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })
    const { token } = await login.json()
    const headers = { Authorization: `Bearer ${token}` }
    const root = mkdtempSync(join(SCRATCH_DIR, prefix))
    populate(root)
    execFileSync('git', ['init', '-q', root])
    const added = await ctx.post(`${API_BASE}/api/repos`, { headers, data: { path: root } })
    expect(added.ok(), await added.text()).toBeTruthy()
    const { repo } = await added.json()
    const listed = await (await ctx.get(`${API_BASE}/api/repos`, { headers })).json()
    const row = listed.repos.find((r) => r.name === repo.name)
    expect(row, `${repo.name} missing from /api/repos`).toBeTruthy()
    return row.id
  } finally {
    await ctx.dispose()
  }
}
