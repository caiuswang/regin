// The rich face's own floating menus. Each takes Esc while it is open, so a
// host that also answers Esc (the focus layout, a dialog) yields to them.
export const EDITOR_OVERLAYS = '[data-ref-menu], [data-slash-menu], [data-bubble-menu]'
