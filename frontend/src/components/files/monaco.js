// The one Monaco entry, reached only through a dynamic import() from a code
// view (I4). It assembles the editor from `editor.api` plus the read-only
// contributions a viewer needs, instead of `editor.main`, which also pulls in
// the TS/JSON/CSS/HTML language services and their workers (D2). Highlighting
// is the Monarch grammars in `basic-languages`, which run on the main thread.
import EditorWorker from 'monaco-editor/editor/editor.worker.js?worker'
import * as monaco from 'monaco-editor/editor/editor.api.js'
import 'monaco-editor/editor/browser/coreCommands.js'
import 'monaco-editor/features/codeEditor/register.js'
import 'monaco-editor/features/codicon/register.js'
import 'monaco-editor/editor/common/standaloneStrings.js'
import 'monaco-editor/features/bracketMatching/register.js'
import 'monaco-editor/features/clipboard/register.js'
import 'monaco-editor/features/contextmenu/register.js'
import 'monaco-editor/features/cursorUndo/register.js'
import 'monaco-editor/features/find/register.js'
import 'monaco-editor/features/folding/register.js'
import 'monaco-editor/features/fontZoom/register.js'
import 'monaco-editor/features/gotoLine/register.js'
import 'monaco-editor/features/lineSelection/register.js'
import 'monaco-editor/features/links/register.js'
import 'monaco-editor/features/longLinesHelper/register.js'
import 'monaco-editor/features/readOnlyMessage/register.js'
import 'monaco-editor/features/smartSelect/register.js'
import 'monaco-editor/features/stickyScroll/register.js'
import 'monaco-editor/features/tokenization/register.js'
import 'monaco-editor/features/unicodeHighlighter/register.js'
import 'monaco-editor/features/wordHighlighter/register.js'
import 'monaco-editor/features/wordOperations/register.js'
import 'monaco-editor/basic-languages/monaco.contribution.js'

// Every label gets the base editor worker: no language-service worker exists
// in this build, so there is nothing else to hand out.
self.MonacoEnvironment = { getWorker: () => new EditorWorker() }

export default monaco
