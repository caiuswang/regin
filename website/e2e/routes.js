// Every route in the F154 spec's `Route` type, plus one path that must fall
// through to the 404.
export const DOCS_ROUTES = [
  '/getting-started',
  '/pipeline',
  '/runs',
  '/dashboard',
  '/guidance',
  '/architecture',
  '/configuration',
  '/cli',
  '/topics',
  '/topics/routing',
  '/topics/proposals',
  '/topics/evolution',
  '/topics/memory',
]

export const NOT_FOUND_ROUTE = '/no-such-page'

export const ALL_ROUTES = ['/', ...DOCS_ROUTES, NOT_FOUND_ROUTE]
