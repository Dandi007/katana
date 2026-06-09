import { parse, HTMLElement } from "node-html-parser";
import { randomUUID } from "node:crypto";
import { nodeHash } from "../ast/hash";
import type { AstNode, DocModel, InlineRun, InlineMark } from "../ast/types";

// ---------------------------------------------------------------------------
// Tag classification
// ---------------------------------------------------------------------------

/** Block tags whose children are other block elements (containers). */
const CONTAINER_TAGS = new Set([
  "callout", "ul", "ol", "table", "colgroup", "thead", "tbody", "tr",
  "th", "td", "grid", "column", "blockquote",
]);

/** Block tags whose content is raw inline HTML (leaf text blocks). */
const INLINE_BLOCK_TAGS = new Set([
  "p", "h1", "h2", "h3", "h4", "h5", "h6", "checkbox", "li",
]);

/** Resource blocks: no children, no text — props only. */
const RESOURCE_TAGS = new Set(["sheet", "bitable", "whiteboard"]);

/** Pre/code block: raw text with possible <code>...</code> wrapper. */
const PRE_TAGS = new Set(["pre"]);

/** Self-closing / empty leaf blocks. */
const EMPTY_TAGS = new Set(["hr", "br"]);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeId(): string {
  return "n-" + randomUUID().slice(0, 8);
}

function getAttrsExcept(el: HTMLElement, ...exclude: string[]): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(el.attrs)) {
    if (!exclude.includes(k)) result[k] = v;
  }
  return result;
}

// ---------------------------------------------------------------------------
// Inline run extraction
// ---------------------------------------------------------------------------

const INLINE_MARK_TAGS: Record<string, InlineMark> = {
  B: "b", STRONG: "b",
  EM: "em", I: "em",
  DEL: "del", S: "del",
  U: "u",
  CODE: "code",
  SPAN: "span",
  A: "a",
};

/**
 * Recursively collect InlineRun[] from an element's child nodes.
 * `inheritedMarks` carries marks from ancestor inline elements.
 */
function collectRuns(el: HTMLElement, inheritedMarks: InlineMark[] = []): InlineRun[] {
  const runs: InlineRun[] = [];

  for (const child of el.childNodes) {
    if (child.nodeType === 3) {
      // text node
      const text = child.text;
      if (text) {
        runs.push({ text, marks: [...inheritedMarks] });
      }
    } else if (child.nodeType === 1) {
      const childEl = child as HTMLElement;
      const tag = childEl.rawTagName?.toUpperCase() ?? "";
      const mark = INLINE_MARK_TAGS[tag];

      if (mark) {
        // inline element — recurse, adding the mark
        const newMarks = inheritedMarks.includes(mark)
          ? inheritedMarks
          : [...inheritedMarks, mark];

        const attrs: Record<string, string> | undefined =
          mark === "a" || mark === "span"
            ? { ...childEl.attrs }
            : undefined;

        // gather sub-runs
        const subRuns = collectRuns(childEl, newMarks);
        if (subRuns.length === 0) {
          // leaf inline with no text children (shouldn't normally happen)
          const text = childEl.text;
          if (text) {
            runs.push({ text, marks: newMarks, ...(attrs ? { attrs } : {}) });
          }
        } else {
          // attach attrs to the innermost runs (or if single run)
          if (attrs && Object.keys(attrs).length > 0) {
            for (const r of subRuns) {
              r.attrs = { ...(r.attrs ?? {}), ...attrs };
            }
          }
          runs.push(...subRuns);
        }
      } else if (tag === "LATEX") {
        // latex inline — treat as a span-like run
        const text = childEl.text;
        if (text) {
          runs.push({ text, marks: [...inheritedMarks, "span"], attrs: { "data-latex": "true" } });
        }
      } else {
        // unknown inline — fall through to text extraction
        const text = childEl.text;
        if (text) {
          runs.push({ text, marks: [...inheritedMarks] });
        }
      }
    }
  }

  return runs;
}

// ---------------------------------------------------------------------------
// Block element → AstNode
// ---------------------------------------------------------------------------

function buildNode(el: HTMLElement): AstNode | null {
  const tag = el.rawTagName?.toLowerCase() ?? "";
  if (!tag) return null;

  const feishuBlockId = el.getAttribute("id") ?? "";
  const id = makeId();

  // --- resource blocks ---
  if (RESOURCE_TAGS.has(tag)) {
    const props = getAttrsExcept(el, "id");
    const node: AstNode = {
      id,
      feishuBlockId,
      type: tag,
      props,
      text: [],
      children: [],
      localHash: "",
      feishuSyncedHash: "",
    };
    const h = nodeHash(node);
    node.localHash = h;
    node.feishuSyncedHash = h;
    return node;
  }

  // --- empty / self-closing blocks ---
  if (EMPTY_TAGS.has(tag)) {
    const props = getAttrsExcept(el, "id");
    const node: AstNode = {
      id,
      feishuBlockId,
      type: tag,
      props,
      text: [],
      children: [],
      localHash: "",
      feishuSyncedHash: "",
    };
    const h = nodeHash(node);
    node.localHash = h;
    node.feishuSyncedHash = h;
    return node;
  }

  // --- pre / code block ---
  if (PRE_TAGS.has(tag)) {
    const props = getAttrsExcept(el, "id");
    // node-html-parser treats <pre> content as raw text; inner <code> tag is not parsed
    const rawText = el.childNodes[0]?.text ?? "";
    // strip wrapping <code>...</code> if present
    const codeText = rawText.replace(/^<code>/, "").replace(/<\/code>$/, "");
    const text: InlineRun[] = codeText ? [{ text: codeText, marks: [] }] : [];
    const node: AstNode = {
      id,
      feishuBlockId,
      type: tag,
      props,
      text,
      children: [],
      localHash: "",
      feishuSyncedHash: "",
    };
    const h = nodeHash(node);
    node.localHash = h;
    node.feishuSyncedHash = h;
    return node;
  }

  // --- container blocks ---
  if (CONTAINER_TAGS.has(tag)) {
    const props = getAttrsExcept(el, "id");
    const children: AstNode[] = [];
    for (const child of el.childNodes) {
      if (child.nodeType !== 1) continue;
      const childEl = child as HTMLElement;
      const childTag = childEl.rawTagName?.toLowerCase() ?? "";
      // skip colgroup — not a meaningful block
      if (childTag === "colgroup") continue;
      const childNode = buildNode(childEl);
      if (childNode) children.push(childNode);
    }
    const node: AstNode = {
      id,
      feishuBlockId,
      type: tag,
      props,
      text: [],
      children,
      localHash: "",
      feishuSyncedHash: "",
    };
    const h = nodeHash(node);
    node.localHash = h;
    node.feishuSyncedHash = h;
    return node;
  }

  // --- inline-content leaf blocks ---
  if (INLINE_BLOCK_TAGS.has(tag)) {
    const props = getAttrsExcept(el, "id");
    const text = collectRuns(el);
    const node: AstNode = {
      id,
      feishuBlockId,
      type: tag,
      props,
      text,
      children: [],
      localHash: "",
      feishuSyncedHash: "",
    };
    const h = nodeHash(node);
    node.localHash = h;
    node.feishuSyncedHash = h;
    return node;
  }

  // --- fallback: treat as inline-content block ---
  const props = getAttrsExcept(el, "id");
  const text = collectRuns(el);
  const node: AstNode = {
    id,
    feishuBlockId,
    type: tag,
    props,
    text,
    children: [],
    localHash: "",
    feishuSyncedHash: "",
  };
  const h = nodeHash(node);
  node.localHash = h;
  node.feishuSyncedHash = h;
  return node;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export function parseContent(content: string, docId: string): DocModel {
  const root = parse(content);

  let title = "";
  const nodes: AstNode[] = [];

  for (const child of root.childNodes) {
    if (child.nodeType !== 1) continue;
    const el = child as HTMLElement;
    const tag = el.rawTagName?.toLowerCase() ?? "";

    if (tag === "title") {
      title = el.text;
      continue;
    }

    const node = buildNode(el);
    if (node) nodes.push(node);
  }

  return {
    docId,
    feishuDocToken: docId,
    title,
    root: nodes,
  };
}
