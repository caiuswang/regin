Found while resolving [Who may read a ledger, and who may declare into it](<https://linear.app/caiuswang/issue/CAI-246>); ruled out of scope for that effort and filed here.

## What

`web/app.py:315-341` lists 20 trace endpoints in `ADMIN_API_ENDPOINTS`, applied centrally at `web/app.py:421`. Two siblings on the same surface are missing from it:

* `/api/sessions/<id>/subtrees` — `trace.api_session_subtrees`, `web/blueprints/trace/sessions.py:2210-2211`. No decorator, not in the admin list, so it is **authenticated-any-role, viewer included**. It returns deep span subtrees — the same projection shape as `/map`'s children, i.e. span content.
* `/api/sessions/<trace_id>/grades` — `web/blueprints/grades.py:55`. Same: auth-only.

The SPA's `/trace/sessions/<sid>` view calls `/subtrees` from `useTraceData.js:547, 827` alongside `/map`, `/spans/<span>/children` and `/ancestors` — all three of which *are* admin-gated. So the fence is complete except for this one endpoint, and a viewer with a token can read span subtrees directly even though `frontend/src/router.js:134-139` redirects them away from `/trace*` in the UI.

The stated rationale for the whole surface being admin-only (`web/app.py:310-314`) is that sessions carry no per-user owner to filter on — which applies to `/subtrees` exactly as much as to its siblings.

## Why it is not a ledger problem

The feature-ledger surface decided ([CAI-246](https://linear.app/caiuswang/issue/CAI-246/who-may-read-a-ledger-and-who-may-declare-into-it)) to render a session row without its deep-link for non-admins, precisely because a session **title** is a label while a session **transcript** is every prompt and response in it. That decision stands whether or not this hole exists; widening the ledger's exposure to match a bug would be the wrong direction. This is a pre-existing gap in the trace blueprint with its own root cause.

## Acceptance

* a viewer token gets 403 from `/api/sessions/<id>/subtrees`
* the same for `/api/sessions/<trace_id>/grades`, or an explicit written reason why grades are deliberately broader

`tests/web/test_blueprint_auth.py:370-407` already pins the admin-only set and is where the assertion belongs. Note `web/app.py:393-400` validates both frozensets against `app.view_functions` at boot, so a typo'd endpoint name fails loudly rather than silently un-gating.