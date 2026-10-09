/**
 * Feature #155 R4 — features link to each other as phases (`follows`) or
 * peers (`related`): the Related section on the feature page, the
 * "New follow-up" button, and the follows chip on the Features list.
 *
 * Live against the suite's server: every claim is about rows the server
 * wrote, so nothing here is stubbed.
 */
import { test, expect } from './auth-fixture.js'
import { TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'

const unique = (label) => `${label} ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`

async function api(request, method, path, data) {
  const { token } = await (await request.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })).json()
  const res = await request[method](`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${token}` }, data,
  })
  expect(res.ok(), `${method} ${path}`).toBeTruthy()
  return res.json()
}

const newFeature = (request, label, extra = {}) =>
  api(request, 'post', '/api/features', { title: unique(label), ...extra })

test('test_feature_page_related_section', async ({ page, request }) => {
  const earliest = await newFeature(request, 'Phase A')
  const middle = await newFeature(request, 'Phase B', { follows: earliest.id })
  const latest = await newFeature(request, 'Phase C', { follows: middle.id })
  const peer = await newFeature(request, 'Peer')

  await page.goto(`/features/${middle.id}`)

  const related = page.getByTestId('feature-related')
  await expect(related.getByTestId(`related-follows-${earliest.id}`)).toContainText(`#${earliest.id}`)
  await expect(related.getByTestId(`related-followed_by-${latest.id}`)).toContainText(`#${latest.id}`)
  await expect(page.getByTestId('section-nav-related')).toContainText('2')

  await related.getByTestId(`related-remove-follows-${earliest.id}`).click()
  await expect(related.getByTestId(`related-follows-${earliest.id}`)).toHaveCount(0)

  await related.getByTestId('related-kind').click()
  await page.getByRole('option', { name: 'Related to' }).click()
  await related.getByTestId('related-query').fill(`#${peer.id}`)
  await related.getByTestId('related-search').click()
  await related.getByTestId(`related-pick-${peer.id}`).click()
  await expect(related.getByTestId(`related-related-${peer.id}`)).toContainText(peer.title)

  const ledger = await api(request, 'get', `/api/features/${middle.id}`)
  expect(ledger.links.follows).toEqual([])
  expect(ledger.links.related.map(r => r.id)).toEqual([peer.id])
})

test('test_new_follow_up_links_feature', async ({ page, request }) => {
  const earlier = await newFeature(request, 'Earlier phase')
  await page.goto(`/features/${earlier.id}`)

  await page.getByTestId('section-nav-new-follow-up').click()
  const dialog = page.getByTestId('new-feature')
  await expect(dialog.getByTestId('declare-follows')).toContainText(`#${earlier.id}`)
  const title = unique('Next phase')
  await dialog.getByTestId('declare-title').fill(title)
  await dialog.getByTestId('declare-submit').click()

  await expect(page).not.toHaveURL(new RegExp(`/features/${earlier.id}$`))
  await expect(page).toHaveURL(/\/features\/\d+$/)
  const newId = Number(new URL(page.url()).pathname.split('/').pop())
  await expect(page.getByTestId(`related-follows-${earlier.id}`)).toBeVisible()

  const ledger = await api(request, 'get', `/api/features/${newId}`)
  expect(ledger.title).toBe(title)
  expect(ledger.links.follows.map(r => r.id)).toEqual([earlier.id])

  await page.goto(`/features?q=${encodeURIComponent(title)}`)
  const chip = page.getByTestId(`feature-follows-${earlier.id}`).first()
  await expect(chip).toHaveText(`follows #${earlier.id}`)
  await chip.click()
  await expect(page).toHaveURL(new RegExp(`/features/${earlier.id}$`))
})
