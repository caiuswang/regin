<script setup>
// The reader's body: every block the span carries, in ONE fixed order —
// prose → diff → bash edits → shell → rules → children. Nothing is behind a
// tab or a disclosure; a single keystroke showing everything is the whole
// point.
import PresentProse from './PresentProse.vue'
import PresentBashEdits from './PresentBashEdits.vue'
import DiffBlock from '../DiffBlock.vue'
import Button from '../ui/Button.vue'
import { vHighlightText } from '../../directives/highlightText.js'

defineProps({
  // presentFor() view model
  vm: { type: Object, required: true },
  // Only the reader knows which session it is reading; the view model is pure
  // over the span, and the image-bytes endpoint is keyed on both.
  traceId: { type: String, default: '' },
  // Whether the "content only" walk filter is on, so a bodyless span can say
  // what ↑↓ will do with spans like it rather than leaving the reader to guess
  // why the walk never stops here.
  onlyBody: { type: Boolean, default: true },
  // A term to mark in the rendered text. Every block below is pure over the
  // span — prose arrives as v-html — so there is no string seam to highlight
  // at, and the directive marks the painted text nodes instead. Empty leaves
  // the DOM untouched: the reader must not carry search chrome.
  //
  // The directive unwraps its marks in THIS element's `beforeUpdate`, so every
  // re-render of the blocks below must flow through here. Giving any descendant
  // its own async/self-driven state would let it patch text nodes while marks
  // are live, and a match at offset 0 leaves Vue holding the emptied head node
  // — the patch then writes there and the old text shows twice.
  highlight: { type: String, default: '' },
})
defineEmits(['open-child'])
</script>

<template>
  <div v-highlight-text="highlight" class="present-content">
    <div v-if="vm.contentLabel" class="present-block-label">{{ vm.contentLabel }}</div>

    <!-- Prose renders through the same markdown component the feed uses, at
         reader scale, with any attached image inlined where its marker sits.
         Reasoning is deliberately upright here: italics is a feed-level tier
         cue, and the reader is for reading. -->
    <!-- A verbatim body is a typed prompt, a file, a git status, an injected
         block — literal text, not model markdown. Through the markdown
         pipeline its indentation collapses and anything mark-shaped
         disappears. It goes through PresentProse all the same, so an attached
         image still lands where its marker sits. -->
    <div v-if="vm.hasProse" class="present-prose">
      <PresentProse
        :markdown="vm.prose"
        :verbatim="vm.proseVerbatim"
        :trace-id="traceId"
        :span-id="vm.spanId"
        :image-indices="vm.imageIndices"
      />
    </div>

    <!-- The deny panel and the MCP round-trip: `visibleAttributeKeys` strips
         both key sets from the attributes table on the detail rail's
         authority, so without a block of their own the payload rendered
         NOWHERE and the reader said "metadata only" beside a rail showing it. -->
    <div v-if="vm.hasDeny" class="present-deny">
      <div class="present-block-label">
        Denied<span class="present-deny-kind">{{ vm.deny.label }}</span>
      </div>
      <p v-if="vm.deny.reason" class="present-deny-reason">{{ vm.deny.reason }}</p>
      <p v-else class="present-deny-reason is-empty">No reason was recorded.</p>
      <p v-if="vm.deny.droppedBytes" class="present-deny-dropped">
        {{ vm.deny.droppedBytes }} B dropped at capture
      </p>
    </div>

    <div v-if="vm.hasMcp" class="present-mcp">
      <div v-if="vm.mcp.input" class="present-mcp-part">
        <div class="present-block-label">
          Request<span v-if="vm.mcp.inputDropped" class="present-mcp-dropped">{{ vm.mcp.inputDropped }} B dropped</span>
        </div>
        <pre class="present-verbatim">{{ vm.mcp.input }}</pre>
      </div>
      <div v-if="vm.mcp.result" class="present-mcp-part">
        <div class="present-block-label">
          Response<span v-if="vm.mcp.resultDropped" class="present-mcp-dropped">{{ vm.mcp.resultDropped }} B dropped</span>
        </div>
        <pre class="present-verbatim">{{ vm.mcp.result }}</pre>
      </div>
    </div>

    <!-- The Q&A round-trip, at reader scale. The feed card is the reference
         for WHAT it shows; the palette is tokens rather than the card's fixed
         slate/amber, because the reader also renders in the dark theme. -->
    <div v-if="vm.hasAsk" class="present-ask">
      <div class="present-block-label">
        Questions &amp; answers
        <span v-if="vm.ask.denied" class="present-ask-deny">{{ vm.ask.denyLabel }}</span>
      </div>
      <div
        v-for="q in vm.ask.questions"
        :key="q.key"
        class="present-ask-q"
        :class="vm.ask.denied ? 'is-denied' : ''"
      >
        <div class="present-ask-head">
          <div v-if="q.header" class="present-ask-header">{{ q.header }}</div>
          <div class="present-ask-question">{{ q.question }}</div>
          <div class="present-ask-mode" :class="q.multi ? 'is-multi' : ''">
            <span aria-hidden="true">{{ q.modeMark }}</span>{{ q.modeLabel }}
          </div>
        </div>
        <ul class="present-ask-opts">
          <li
            v-for="opt in q.options"
            :key="opt.key"
            class="present-ask-opt"
            :class="opt.chosen ? 'is-chosen' : ''"
          >
            <span class="present-ask-mark" aria-hidden="true">{{ opt.mark }}</span>
            <span class="present-ask-opt-body">
              <span class="present-ask-label">{{ opt.label }}</span>
              <span v-if="opt.description" class="present-ask-desc">{{ opt.description }}</span>
              <details v-if="opt.preview" class="present-ask-preview">
                <summary>Preview</summary>
                <pre>{{ opt.preview }}</pre>
              </details>
            </span>
          </li>
          <li v-if="q.freeText" class="present-ask-opt is-free">
            <span class="present-ask-mark" aria-hidden="true">✎</span>
            <span class="present-ask-opt-body">{{ q.freeText }}</span>
          </li>
        </ul>
        <div v-if="q.note" class="present-ask-note">Note: {{ q.note }}</div>
      </div>
      <div v-if="vm.ask.denialReason" class="present-ask-denial">
        <div
          class="present-ask-denial-label"
          title="Templated text the agent harness injects when the user denies a tool call — not user prose."
        >Denied (agent injected prompt)</div>
        {{ vm.ask.denialReason }}
      </div>
    </div>

    <!-- `code-surface`: DiffBlock paints its gutter and context lines with the
         light end of the slate ramp, which inversion would darken onto the
         equally dark well. -->
    <div v-if="vm.hasDiff" class="present-diff code-surface">
      <div class="present-diff-head">
        <span class="present-diff-file">{{ vm.diffFile || 'diff' }}</span>
        <span v-if="vm.diffOp" class="present-diff-op">{{ vm.diffOp }}</span>
        <span v-if="vm.diffAdd != null" class="present-diff-add">+{{ vm.diffAdd }}</span>
        <span v-if="vm.diffDel != null" class="present-diff-del">−{{ vm.diffDel }}</span>
      </div>
      <DiffBlock numbered flush :diff="vm.diff" :file-path="vm.diffFile" />
    </div>

    <!-- What the shell command did to the files it touched. Above the terminal
         block for the same reason the fixed order puts `diff` there: the
         change is the point of the call, the output is its trace. -->
    <PresentBashEdits v-if="vm.hasBashEdits" :edits="vm.bashEdits" />

    <div v-if="vm.hasTerm" class="present-term">
      <div v-if="vm.termCmd" class="present-term-cmd">$ {{ vm.termCmd }}</div>
      <pre v-if="vm.termLines.length" class="present-term-out"><span
        v-for="(line, i) in vm.termLines"
        :key="i"
        class="present-term-line"
        :class="line.color"
      >{{ line.text }}</span></pre>
    </div>

    <div v-if="vm.hasRules" class="present-rules-block">
      <div class="present-block-label">
        Rule check<span v-if="vm.rules.file" class="present-rule-file">{{ vm.rules.file }}</span>
      </div>
      <p class="present-rule-verdict">
        <span :class="vm.rules.violating ? 'is-bad' : 'is-ok'">
          {{ vm.rules.violating }} violated
        </span>
        · {{ vm.rules.applicable }} applicable<template v-if="vm.rules.total"> of {{ vm.rules.total }}</template>
        <template v-if="vm.rules.status"> · {{ vm.rules.status }}</template>
      </p>
      <ul v-if="vm.rules.items.length" class="present-rules">
        <li v-for="rule in vm.rules.items" :key="rule.id" class="present-rule">
          <span class="present-rule-mark" :class="rule.violated ? 'is-bad' : 'is-ok'">{{ rule.mark }}</span>
          <span class="present-rule-body">
            <span class="present-rule-id">{{ rule.id }}</span>
            <span v-if="rule.violated && rule.matchCount > 1" class="present-rule-count">×{{ rule.matchCount }}</span>
            <span v-if="rule.summary" class="present-rule-summary">{{ rule.summary }}</span>
          </span>
        </li>
      </ul>
    </div>

    <ul v-if="vm.hasChildren" class="present-children">
      <li v-for="child in vm.children" :key="child.spanId">
        <Button
          variant="ghost"
          size="sm"
          class="present-child h-auto w-full justify-start gap-2.5 px-2.5 py-2 rounded-[9px]"
          @click="$emit('open-child', child.spanId)"
        >
          <span class="present-child-dot" :class="child.dot" aria-hidden="true"></span>
          <span class="present-child-title" :title="child.title">{{ child.title }}</span>
          <span class="present-child-sub">{{ child.sub }}</span>
          <span class="present-child-meta">{{ child.meta }}</span>
        </Button>
      </li>
    </ul>

    <div v-if="vm.empty" class="present-empty">
      <template v-if="vm.encrypted">
        <p class="present-empty-line">Reasoning returned encrypted — the API sent only a signature.</p>
        <p v-if="vm.encryptedNote" class="present-empty-meta">{{ vm.encryptedNote }}</p>
        <p class="present-empty-walk">The tokens are real and billed; the text was never captured, so there is nothing to render.</p>
      </template>
      <template v-else>
        <p class="present-empty-line">This span carries no body — metadata only.</p>
        <p class="present-empty-walk">{{ onlyBody
          ? '↑↓ skips spans like this — turn off “content only” below to walk them.'
          : '↑↓ walks spans like this.' }}</p>
      </template>
    </div>
  </div>
</template>

<style scoped>
/* `.present-verbatim` is global (assets/style.css): PresentProse paints it too,
   and a scoped rule does not cross into a child component. */

.present-rules-block { margin-bottom: 18px; }
.present-rule-file {
  margin-left: 8px; font-family: var(--font-mono); letter-spacing: 0;
  text-transform: none; color: var(--color-fg-muted); font-weight: 500;
}
.present-rule-verdict {
  margin: 0 0 10px; font-family: var(--font-mono); font-size: 12px;
  color: var(--color-fg-muted);
}
.present-rule-verdict .is-bad { color: var(--color-danger, var(--color-red-600)); font-weight: 700; }
.present-rule-verdict .is-ok { color: var(--color-success, var(--color-emerald-600)); font-weight: 700; }

.present-deny { margin-bottom: 18px; }
.present-deny-kind {
  margin-left: 8px; font-weight: 600; letter-spacing: 0; text-transform: none;
  color: var(--color-warning, var(--color-fg-muted));
}
.present-deny-reason {
  margin: 0; font-size: 14px; line-height: 1.65; color: var(--color-fg);
  text-wrap: pretty;
}
.present-deny-reason.is-empty { color: var(--color-fg-faint); font-style: italic; }
.present-deny-dropped { margin: 6px 0 0; font-size: 11.5px; color: var(--color-fg-faint); }

.present-mcp { margin-bottom: 18px; }
.present-mcp-part + .present-mcp-part { margin-top: 14px; }
.present-mcp-dropped {
  margin-left: 8px; letter-spacing: 0; text-transform: none;
  color: var(--color-fg-faint); font-weight: 500;
}

.present-block-label {
  font-size: 10px; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--color-fg-subtle); margin-bottom: 10px;
}

/* Reader typography. Scoped styles do not cross into MarkdownContent's own
   subtree, so every prose rule needs :deep. */
.present-prose :deep(p) {
  font-size: 15px; line-height: 1.72; color: var(--color-fg); margin-bottom: 14px;
  text-wrap: pretty;
}
.present-prose :deep(h1), .present-prose :deep(h2) {
  font-size: 17px; font-weight: 700; margin: 22px 0 8px; color: var(--color-fg);
}
.present-prose :deep(h3), .present-prose :deep(h4) {
  font-size: 15px; font-weight: 700; margin: 22px 0 8px; color: var(--color-fg);
}
.present-prose :deep(ul), .present-prose :deep(ol) { margin: 0 0 14px 1.25rem; }
.present-prose :deep(li) { font-size: 15px; line-height: 1.72; margin-bottom: 8px; }
.present-prose :deep(code) {
  font-size: 13px; background: var(--color-surface-2); padding: 1px 5px; border-radius: 5px;
}
/* Markdown's own fenced block. `:not(.present-verbatim)` because a literal body
   renders a `pre` here too and must keep WRAPPING — this rule scrolls sideways
   instead, which is the reading surface the verbatim well exists to avoid. */
.present-prose :deep(pre):not(.present-verbatim) {
  border-radius: 11px; background: var(--color-surface-2); color: var(--color-fg);
  padding: 12px 14px;
  font-size: 12.5px; line-height: 1.6; white-space: pre; overflow-x: auto; margin-bottom: 14px;
}
.present-prose :deep(pre code) { background: none; padding: 0; color: inherit; }
.present-prose :deep(table) { display: block; overflow-x: auto; font-size: 13.5px; }
.present-prose :deep(a) { color: var(--color-blue-700); }

.present-ask-deny {
  margin-left: 6px; padding: 1px 5px; border-radius: 4px;
  background: var(--color-warning-soft); color: var(--color-warning-strong);
  border: 1px solid var(--color-amber-200);
}
.present-ask-q {
  border: 1px solid var(--color-border); border-radius: 11px; overflow: hidden;
  background: var(--color-surface); margin-bottom: 12px;
}
.present-ask-q.is-denied { border-color: var(--color-amber-200); }
.present-ask-head {
  padding: 10px 14px; border-bottom: 1px solid var(--color-border);
  background: var(--color-surface-2);
}
.present-ask-q.is-denied .present-ask-head {
  background: var(--color-amber-50); border-bottom-color: var(--color-amber-200);
}
.present-ask-header {
  font-size: 10px; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--color-fg-subtle); margin-bottom: 3px;
}
.present-ask-question { font-size: 14.5px; font-weight: 600; color: var(--color-fg); text-wrap: pretty; }
.present-ask-mode {
  display: inline-flex; align-items: center; gap: 5px; margin-top: 5px;
  padding: 1px 7px; border: 1px solid var(--color-border); border-radius: 999px;
  font-size: 11px; font-weight: 600; color: var(--color-fg-muted);
}
.present-ask-mode.is-multi {
  border-color: var(--color-info);
  background: var(--color-info-soft);
  color: var(--color-info-strong);
}
.present-ask-opts { margin: 0; padding: 0; list-style: none; }
.present-ask-opt {
  display: flex; gap: 10px; align-items: flex-start;
  padding: 8px 14px; border-top: 1px solid var(--color-border);
}
.present-ask-opt:first-child { border-top: 0; }
.present-ask-opt.is-chosen { background: var(--color-success-soft); }
.present-ask-opt.is-free { background: var(--color-warning-soft); }
.present-ask-mark {
  flex-shrink: 0; width: 1rem; text-align: center;
  font-family: var(--font-mono); font-size: 12.5px; color: var(--color-fg-faint);
}
.present-ask-opt.is-chosen .present-ask-mark { color: var(--color-success); }
.present-ask-opt.is-free .present-ask-mark { color: var(--color-warning); }
.present-ask-opt-body { min-width: 0; flex: 1; font-size: 13.5px; color: var(--color-fg); }
.present-ask-label { display: block; font-weight: 600; line-height: 1.5; }
.present-ask-desc {
  display: block; margin-top: 2px; font-size: 13px; line-height: 1.55;
  color: var(--color-fg-muted); font-weight: 400; text-wrap: pretty;
}
.present-ask-preview { margin-top: 6px; }
.present-ask-preview summary {
  cursor: pointer; user-select: none;
  font-size: 10px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--color-fg-subtle);
}
.present-ask-preview summary:hover { color: var(--color-fg-muted); }
.present-ask-preview pre {
  margin: 6px 0 0; padding: 10px 12px; border-radius: 9px;
  background: var(--color-surface-2); color: var(--color-fg-muted);
  font-family: var(--font-mono); font-size: 12px; line-height: 1.6;
  white-space: pre-wrap; overflow-wrap: anywhere; max-height: 20rem; overflow-y: auto;
}
.present-ask-note {
  padding: 8px 14px; border-top: 1px solid var(--color-border);
  background: var(--color-surface-2);
  font-size: 12.5px; font-style: italic; color: var(--color-fg-muted);
}
.present-ask-denial {
  border: 1px solid var(--color-amber-200); border-radius: 11px;
  background: var(--color-amber-50); padding: 12px 14px;
  font-size: 13px; line-height: 1.6; color: var(--color-fg);
  white-space: pre-wrap; overflow-wrap: anywhere;
}
.present-ask-denial-label {
  font-size: 10px; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--color-warning-strong); margin-bottom: 5px;
}

.present-diff, .present-term { margin-top: 22px; }
.present-diff-head {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  font-family: var(--font-mono); font-size: 11.5px; margin-bottom: 6px;
}
.present-diff-file { color: var(--color-fg-muted); word-break: break-all; }
.present-diff-op { color: var(--color-fg-subtle); text-transform: uppercase; letter-spacing: 0.08em; font-size: 10px; }
.present-diff-add { color: var(--color-emerald-600); }
.present-diff-del { color: var(--color-red-600); }
.present-diff :deep(.diff-block) {
  background: var(--code-bg); border-radius: 11px; margin: 0;
}

.present-term-cmd {
  font-family: var(--font-mono); font-size: 12.5px; color: #7dd3a8;
  background: var(--code-bg); border-radius: 11px 11px 0 0; padding: 10px 14px;
  white-space: pre-wrap; word-break: break-word;
}
.present-term-out {
  background: var(--code-bg); border-radius: 0 0 11px 11px; margin: 0; padding: 10px 14px;
  font-family: var(--font-mono); font-size: 12.5px; line-height: 1.6;
  white-space: pre; overflow-x: auto;
}
.present-term-line { display: block; }
.term-echo { color: #7dd3a8; }
.term-fail { color: #fca5a5; }
.term-ok { color: #86efac; }
.term-plain { color: #c3cbd8; }

.present-rules { margin-top: 22px; list-style: none; padding: 0; }
.present-rule {
  display: flex; gap: 10px; align-items: flex-start;
  padding: 7px 0; border-bottom: 1px solid var(--color-border);
}
.present-rule-mark { font-family: var(--font-mono); font-size: 12.5px; width: 1rem; text-align: center; flex-shrink: 0; }
.present-rule-mark.is-ok { color: var(--color-emerald-600); }
.present-rule-mark.is-bad { color: var(--color-red-600); }
.present-rule-body { min-width: 0; }
.present-rule-id { font-family: var(--font-mono); font-size: 12.5px; color: var(--color-fg); }
.present-rule-count { margin-left: 6px; font-family: var(--font-mono); font-size: 11px; color: var(--color-red-600); }
.present-rule-summary { display: block; font-size: 12.5px; color: var(--color-fg-muted); margin-top: 2px; }

.present-children { margin-top: 22px; list-style: none; padding: 0; min-width: 0; }
.present-children li { min-width: 0; }
.present-child { align-items: baseline; text-align: left; max-width: 100%; overflow: hidden; }
.present-child-dot { width: 8px; height: 8px; border-radius: 9999px; flex-shrink: 0; }
/* `min-width:0` lets the BOX shrink but the text still paints outside it — the
   Button primitive's own `whitespace-nowrap` keeps the run on one line — so a
   long Bash command ran clean across the meta column. The clip is what was
   missing, not the flex sizing. Full text stays reachable via `title`. */
.present-child-title {
  font-size: 13.5px; color: var(--color-fg); min-width: 0; flex: 1;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.present-child-sub {
  font-family: var(--font-mono); font-size: 11px; color: var(--color-fg-subtle); flex-shrink: 0;
}
.present-child-meta {
  font-family: var(--font-mono); font-size: 11px; color: var(--color-fg-faint); flex-shrink: 0;
}

.present-empty {
  margin-top: 8px; border: 1px dashed var(--color-border-strong); border-radius: 11px;
  padding: 18px; text-align: center;
}
.present-empty-line { margin: 0; font-size: 13px; color: var(--color-fg-subtle); }
.present-empty-walk { margin: 6px 0 0; font-size: 12.5px; color: var(--color-fg-faint); }
.present-empty-meta {
  margin: 6px 0 0; font-family: var(--font-mono); font-size: 12.5px; color: var(--color-fg-muted);
}
</style>
