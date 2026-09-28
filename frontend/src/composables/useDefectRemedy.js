import api from '../api'

// A defect refusal that names another verb carries it as `remedy`
// (`lib/defects/store.py::remedy`), so the page offers that verb as a button
// rather than quoting the CLI command the sentence spells out.

export const REMEDY_LABEL = {
  merge: a => `Merge #${a.defect_id} into #${a.into}`,
  reopen: a => `Reopen #${a.defect_id}`,
  attribute: a => `Attribute #${a.defect_id}…`,
  fix_create: a => `Create a fix task for #${a.defect_id}`,
}

/** `{ msg, remedy }` off an `api` refusal, or off a plain sentence. */
export function refusalOf(body, fallback = 'the ledger refused, without saying why') {
  if (typeof body === 'string') return { msg: body, remedy: null }
  const remedy = body?.body?.remedy
  return {
    msg: body?.msg || fallback,
    remedy: remedy && REMEDY_LABEL[remedy.verb] ? remedy : null,
  }
}

/**
 * Run a remedy that needs no further input. Resolves to
 * `{ ok, message, keeper? }`, or `{ ok: false, refusal }`, or
 * `{ reveal: 'attribute', defectId }` when the remedy needs a feature picked.
 */
export async function runRemedy(remedy, { confirm } = {}) {
  const { verb, args } = remedy
  const id = args.defect_id
  if (verb === 'attribute') return { reveal: 'attribute', defectId: id }
  if (verb === 'reopen' && confirm) {
    const ok = await confirm('Reopen',
      `Reopen defect #${id}? This records that the fix did not hold and adds a round.`, true)
    if (!ok) return { ok: false, refusal: null }
  }
  const call = {
    merge: () => api.post(`/defects/${id}/merge`, { into: args.into }),
    reopen: () => api.post(`/defects/${id}/reopen`),
    fix_create: () => api.post(`/defects/${id}/fix-item`, { create: true }),
  }[verb]
  const body = await call()
  if (body?.ok === false) return { ok: false, refusal: refusalOf(body) }
  if (verb === 'merge') {
    return { ok: true, keeper: body.defect.id, message: `#${id} folded into #${body.defect.id}.` }
  }
  if (verb === 'reopen') return { ok: true, message: `#${id} reopened at round ${body.defect.round}.` }
  return { ok: true, message: `#${id} will be fixed by task #${body.defect.fix_task_id}.` }
}
