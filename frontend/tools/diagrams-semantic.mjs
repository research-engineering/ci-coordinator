const ACCESSIBILITY_STATES = new Set(["acc_title", "acc_descr", "acc_descr_multiline"]);
const LABEL_STATES = new Set([
  "text",
  "trapText",
  "ellipseText",
  "edgeText",
  "thickEdgeText",
  "dottedEdgeText",
  "string",
  "md_string",
]);
const LABEL_TOKENS = new Set(["TEXT", "STR", "MD_STR", "EDGE_TEXT"]);

function blankText(value) {
  return value.replace(/[^\n]/g, " ");
}

function escapeLabel(value) {
  return value.replace(
    /[\\\r\n[\](){}]/g,
    (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`,
  );
}

export async function projectSemanticBody(body, type) {
  const { default: mermaid } = await import("mermaid");
  mermaid.initialize({ startOnLoad: false, layout: "dagre", theme: "default", look: "classic" });
  const flowchart = type === "flowchart" || type === "graph";
  const diagram = await mermaid.mermaidAPI.getDiagramFromText(
    `${flowchart ? "flowchart TD" : type}\n`,
  );
  // The pinned Mermaid parser exposes its generated lexer through this wrapper.
  // Its states and ranges own text boundaries; no grammar is reimplemented here.
  const parser = diagram.getParser().parser;
  if (!parser?.lexer?.next || !parser.terminals_) {
    throw new Error("the admitted Mermaid lexer is unavailable");
  }
  const lexer = Object.create(parser.lexer);
  lexer.options = { ...lexer.options, ranges: true };
  diagram.db.clear?.();
  // Match Mermaid's comment preprocessor while retaining source coordinates.
  const source = body.replace(/^\s*%%(?!{)[^\n]+\n?/gm, blankText);
  lexer.setInput(source, diagram.db);
  const edits = [];
  let span;
  for (let step = 0; step <= source.length * 2 + 32; step++) {
    const token = lexer.next();
    const name = parser.terminals_[token] ?? token;
    if (token === lexer.EOF || name === "EOF") {
      if (span) throw new Error("Parse error: unclosed Mermaid text boundary");
      let result = "";
      let position = 0;
      for (const [start, end, replacement] of edits) {
        result += source.slice(position, start) + replacement;
        position = end;
      }
      return result + source.slice(position);
    }
    const [start, end] = lexer.yylloc.range;
    const state = lexer.topState();
    if (!span && (ACCESSIBILITY_STATES.has(state) || (flowchart && state === "shapeData"))) {
      span = { kind: "metadata", start, depth: lexer.conditionStack.length };
    } else if (!span && flowchart && LABEL_STATES.has(state)) {
      span = { kind: "label", start, depth: lexer.conditionStack.length, newlines: 0 };
    }
    if (span?.kind === "label" && LABEL_TOKENS.has(name)) {
      const text = source.slice(start, end);
      edits.push([start, end, escapeLabel(text)]);
      span.newlines += text.split("\n").length - 1;
    }
    if (span && lexer.conditionStack.length < span.depth) {
      if (span.kind === "metadata") {
        edits.push([span.start, end, blankText(source.slice(span.start, end))]);
      } else if (span.newlines) {
        // Keep a declaration on its opening line and following tokens on theirs.
        edits.push([end, end, "\n".repeat(span.newlines)]);
      }
      span = undefined;
    }
  }
  throw new Error("the Mermaid lexer exceeded its source-bound token budget");
}
