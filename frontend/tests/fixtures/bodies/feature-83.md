# 整理侧边栏菜单 — one rail entry per idea

## Intent

The left rail had grown to twenty entries, and it was counting the same idea
twice in two places. A **skill is deployed from a pattern**, so Patterns and
Skills are two readings of one library, not two subsystems. **Minting a machine
credential is configuration**, not a destination. Collapse both: one icon each,
with a tab strip or a settings section doing the switching that the rail used to
do.

Non-goals: no URL changes, no renamed APIs, no new capability. This is purely a
navigation-surface consolidation — every existing link, bookmark, spec and
CLI-printed URL must still resolve.

## Shape

```
LibraryShell = { route: '/library', redirect: '/patterns',
                 children: ['/patterns', '/skills'] }   // ABSOLUTE child paths
RailLink     = { to: Path, label: str, icon: str,
                 match?: Path[],      // prefix set the highlight survives
                 exact?: bool }
SettingsSection = 'config' | 'providers' | … | 'api-keys' | 'debug'
```

## Decisions

| Question | Decision | Why |
|---|---|---|
| Where do the tabs live? | A `LibraryView` shell mirroring `PipelineView` | The pipeline already solved this exact shape (tasks/features/defects behind one entry); a second bespoke pattern would be two answers to one question |
| Child route paths | Absolute (`/patterns`, `/skills`) under `/library` | The grouping is new, the URLs are not |
| Detail pages (`/patterns/:slug`, `/skills/:id`) | Stay **siblings**, outside the shell | A document is not a third reading of the list, and it carries its own identity header. (Pipeline made its details children because a *row's permalink* is still the list's business; a pattern body is not.) |
| KeepAlive on the tabs? | **No** | A deactivated view's `<Teleport>` content survives in place, so a cached tab would leave its buttons in the header of the tab you switched to |
| Where do the child's actions render? | Teleported into the shell header's `#library-actions` | Every page's actions sit where the eye already looks |
| API keys as a rail link with `?section=` | **No** — the rail entry is deleted | `isActiveLink`/`goTo` compare `route.path` only, so a query-carrying entry could never highlight and could never be re-navigated |
| Legacy `/api-keys` | `redirect` → `/settings?section=api-keys` | Bookmarks and old docs keep working; `Settings` carries `match: ['/settings','/api-keys']` so the rail stays lit while it resolves |
| API-keys section visibility | Nav button `v-if="isAdmin"`, section keeps its own `keys-forbidden` refusal | Hiding a button is not a guard; `/api/api-keys` is `require_role('admin')` |

## Invariants

- Exactly one rail item is `is-active` on any route the rail can reach.
- No two rail entries describe the same underlying object.
- A tab switch leaves nothing of the previous tab in the shared header.
- Every pre-change URL still resolves, directly or by redirect.

## Acceptance

```gherkin
Scenario: one library entry
  Given the sidebar
  Then it shows exactly one "Patterns & Skills" entry and no "Skills" entry

Scenario: the tab strip is the switcher
  Given I am on /patterns
  When I click the "Skills" tab
  Then the URL is /skills, the Skills tab is is-active,
   And the "New pattern" button is gone from the header
   And exactly one "New skill" button is in the header

Scenario: the rail survives a drill-down
  Given I open /patterns/<slug>
  Then the "Patterns & Skills" entry is still is-active with aria-current="page"

Scenario: keys are a settings section
  Given I open the legacy /api-keys
  Then I land on /settings?section=api-keys with the mint form,
   And the rail lights "Settings", not a removed "API keys" entry

Scenario: hiding a button is not a guard
  Given a non-admin deep-links /settings?section=api-keys
  Then no "API keys" section button exists
   And the section renders keys-forbidden and no mint control

Scenario: the new chrome paints clean
  Given /patterns and /skills, in light and dark, at 1440px and 390px
  Then the console is empty and the h1 reads "Patterns & Skills"
```

## Known follow-up (not built)

The rail's `nav.sb-nav` still overflows its scroll container at 900px viewport
height — the System group's label paints while `Settings` sits just below the
fold. This change *reduced* the overflow by two rows (695px content in a 643px
box, down from ~771px) but did not close it. Pre-existing; a separate defect.
