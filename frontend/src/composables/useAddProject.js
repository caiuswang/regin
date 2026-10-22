import api from '../api'

export const MODE_REPO = 'repo'
export const MODE_WORKSPACE = 'workspace'

// A `Select` value no path can take: picking it opens the dialog instead of
// becoming the working directory.
export const ADD_PROJECT_VALUE = '::add-project::'

export const NOT_FOUND = '路径不存在'
export const NO_REPOS = '不是 git 仓库，其下也没有 git 仓库'

/** Registering a repo or a workspace needs editor; with auth off there is no
 * stored user, and the server lets that request through. */
export function canAddProjects() {
  return api.getStoredUser()?.role !== 'viewer'
}

/**
 * The AddProjectDialog branch table, read off one `GET /api/repos/probe`.
 *
 * Returns `{ modes, error }`: the modes the operator may submit in, first one
 * the default, or an `error` that blocks submit. An existing workspace root
 * answers `{ existing: true }` so the caller can name it.
 */
export function probeVerdict(probe) {
  if (!probe.exists) return { modes: [], error: NOT_FOUND }
  if (probe.workspace_id != null) return { modes: [], error: '', existing: true }
  const children = probe.child_repos?.length || 0
  if (probe.is_git) {
    return { modes: children ? [MODE_REPO, MODE_WORKSPACE] : [MODE_REPO], error: '' }
  }
  return children ? { modes: [MODE_WORKSPACE], error: '' } : { modes: [], error: NO_REPOS }
}

/** Working-directory options with "+ Add project…" ruled off at the end. */
export function withAddProject(options) {
  if (!canAddProjects()) return options
  return [...options, { separator: true }, { value: ADD_PROJECT_VALUE, label: '+ Add project…' }]
}
