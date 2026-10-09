// DocBlock constructors — the prototype's CONTENT shorthand. A page is a list
// of [section title, DocBlock[]] pairs rendered by DocSections.

export const P = (t) => ({ k: 'p', t })
export const H = (t) => ({ k: 'h3', t })
export const C = (t) => ({ k: 'code', t })
export const N = (tone, t) => ({ k: 'note', tone, t })
export const S = (src, alt, cap) => ({ k: 'shot', src, alt, cap })
export const L = (items, ordered = false) => ({ k: 'list', items, ordered })
export const T = (cols, grid, rows, minW, mono = [0]) => ({ k: 'table', cols, grid, rows, minW, mono })
