// `lib/features/index.py::SORT_KEYS` and `DEFAULT_SORT_KEY`. Restated rather
// than fetched because a select whose options arrive late renders a control
// with nothing in it; `tests/features/test_feature_sort_parity.py` holds the
// two lists equal.
export const FEATURE_SORT_OPTIONS = [
  { value: 'created:desc', label: 'Newest ↓' },
  { value: 'activity:desc', label: 'Last activity ↓' },
  { value: 'stage:asc', label: 'Lifecycle' },
  { value: 'rounds:desc', label: 'Rounds ↓' },
  { value: 'open_defects:desc', label: 'Open defects ↓' },
  { value: 'escapes:desc', label: 'Escapes ↓' },
  { value: 'cost:desc', label: 'Cost ↓' },
]

export const DEFAULT_FEATURE_SORT = 'created:desc'

export const FEATURE_SORT_STORAGE_KEY = 'regin.features.sort'
