## Spec

The New-task composer accepts a tracker reference (a Linear issue URL or a bare key) as its first field. Pasting one and resolving it fills the title from the issue, and the details from the issue description when both are still empty — so capturing a task that already exists in the tracker takes a paste and a click, with nothing retyped. Whatever is prefilled stays editable; a resolve never overwrites text the operator already put in.

Resolution is a preview: it reads the issue and shows what it found without writing anything. No row, no link, no half-created task if the operator changes their mind and closes the dialog. On capture, the resolved reference is attached to the new task as a normal external link, so the task detail page renders it exactly like a link attached after the fact.

It stays provider-agnostic. The field is driven by whatever trackers report themselves configured — Linear today — and the ambiguity and not-configured answers are the same ones the existing attach field gives. With no tracker configured the field states that rather than disappearing.

## Acceptance items (tests)

* `test_preview_returns_issue_and_creates_no_link`
* `test_preview_ref_required`
* `test_preview_unknown_ref_is_400`
* `test_preview_ambiguous_ref_is_409_naming_candidates`
* `test_preview_fetch_failure_is_400_and_creates_no_link`
* `test_preview_requires_editor`
* E2E `composer resolves a pasted ref and prefills title and details`
* E2E `composer leaves an already-typed title alone when a ref resolves`
* E2E `capturing with a resolved ref attaches the link to the new task`

Add a linea task link so let we could just quicky create a task without input other field(include title, you could auto sync from the linear)

Element:   div.ds-dialog.ds-dialog-wide

Text:      "New taskCapture an idea. Nothing runs until someone arms it.Title Details (optio"

Route:     /tasks

Box:       1152×493 at (259, 234)

Selector:  div:nth-child(11)

Diagnose with:

node scripts/dom-measure.mjs \

--route '/tasks' \

--reveal 'wait:3000' \

--explain 'div:nth-child(11)'