/**
 * Write a throwaway git repo under the suite's scratch dir and register it
 * through POST /api/repos, returning its project id.
 *
 * Registering is a read-modify-write of `repo_paths` (lib/sync/repo_discovery.py),
 * so two spec files registering at once can each drop the other's row. The
 * loser sees its name missing from the listing and registers a fresh copy at a
 * new path: the old path may still sit in `repo_paths`, where re-adding it is
 * refused as a duplicate.
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync } from 'node:fs'
import { join } from 'node:path'
import { expect } from '@playwright/test'
import { SCRATCH_DIR, TEST_USER } from '../../e2e-env.js'
import { API_BASE } from './api-base.js'

const ATTEMPTS = 3

export async function registerFixtureRepo(playwright, prefix, populate) {
  mkdirSync(SCRATCH_DIR, { recursive: true })
  const ctx = await playwright.request.newContext()
  try {
    const login = await ctx.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })
    const { token } = await login.json()
    const headers = { Authorization: `Bearer ${token}` }
    for (let attempt = 0; attempt < ATTEMPTS; attempt++) {
      const root = mkdtempSync(join(SCRATCH_DIR, prefix))
      populate(root)
      execFileSync('git', ['init', '-q', root])
      const added = await ctx.post(`${API_BASE}/api/repos`, { headers, data: { path: root } })
      expect(added.ok(), await added.text()).toBeTruthy()
      const { repo } = await added.json()
      const listed = await (await ctx.get(`${API_BASE}/api/repos`, { headers })).json()
      const row = listed.repos.find((r) => r.name === repo.name)
      if (row) return row.id
    }
    throw new Error(`fixture repo ${prefix} was dropped from /api/repos ${ATTEMPTS} times`)
  } finally {
    await ctx.dispose()
  }
}
