// upstream/components/theme.ts
var COLOR_ROLES = ["text", "dim", "accent", "brand", "code", "success", "warning", "error"];
var ATTRIBUTE_ROLES = ["bold", "italic", "underline", "strike", "selected"];
function paletteSpec(scheme) {
  return {
    colors: {
      // The terminal's own foreground, emitted as no escape at all: ordinary body
      // text must inherit whatever the user's theme uses.
      text: { open: "", close: "", purpose: "Body text, the terminal default foreground" },
      // SGR 2 over an explicit default foreground, closing both groups it sets.
      // The attribute fades relative to whatever the terminal's own foreground is,
      // which is the only way to land *below* `text` on both schemes: ANSI 90
      // (bright black) is a fixed hue that many light themes render heavier than
      // their default foreground, which made every "dim" surface the most
      // prominent text on screen.
      dim: { open: "2;39", close: "22;39", purpose: "The one recessed tone: tool bodies, chrome, footers" },
      accent: { open: "95", close: "39", purpose: "The one emphasis color: role headers, prompt, borders" },
      brand: { open: "34", close: "39", purpose: "DeepSeek brand art when truecolor is unavailable" },
      // ANSI 36 (cyan) is difficult to read on a light background — use ANSI 34
      // (blue) which is legible on both light and dark schemes.
      code: scheme === "light" ? { open: "34", close: "39", purpose: "Inline code and code blocks in prose" } : { open: "36", close: "39", purpose: "Inline code and code blocks in prose" },
      success: { open: "32", close: "39", purpose: "Succeeded calls, and a diff's added lines" },
      warning: { open: "33", close: "39", purpose: "Pending calls and warnings" },
      error: { open: "31", close: "39", purpose: "Failures, signals, and a diff's removed lines" }
    },
    attributes: {
      bold: { open: "1", close: "22", purpose: "Emphasis; composes with any color" },
      italic: { open: "3", close: "23", purpose: "Reasoning text" },
      underline: { open: "4", close: "24", purpose: "Role-header banding" },
      strike: { open: "9", close: "29", purpose: "Struck-through Markdown" },
      selected: { open: "7", close: "27", purpose: "Reverse video for the active selection" }
    }
  };
}
function ansi(spec, enabled) {
  if (!enabled || spec.open === "") return (text) => text;
  return (text) => `\x1B[${spec.open}m${text}\x1B[${spec.close}m`;
}
function createPalette(enabled, scheme = "dark") {
  const spec = paletteSpec(scheme);
  const roles = {};
  for (const name of COLOR_ROLES) roles[name] = ansi(spec.colors[name], enabled);
  for (const name of ATTRIBUTE_ROLES) roles[name] = ansi(spec.attributes[name], enabled);
  return roles;
}
var BRAND_GRADIENT = [
  [77, 107, 254],
  // #4D6BFE
  [57, 130, 255],
  // #3982FF
  [36, 152, 255]
  // #2498FF
];
var DEEPSEEK_BRAND_RGB = BRAND_GRADIENT[0];
function markdownTheme(palette) {
  return {
    heading: (text) => palette.accent(text),
    link: (text) => palette.accent(text),
    // pi-tui requires this URL slot but its current Markdown renderer does not invoke it.
    /* v8 ignore next */
    linkUrl: (text) => palette.dim(text),
    code: (text) => palette.code(text),
    codeBlock: (text) => palette.code(text),
    // pi-tui presents both fence rows through this callback. Keep the opening
    // language label, but hide Markdown syntax and the otherwise-empty close.
    codeBlockBorder: (text) => palette.dim(text.slice(3)),
    quote: (text) => palette.dim(text),
    quoteBorder: (text) => palette.accent(text),
    hr: (text) => palette.dim(text),
    listBullet: (text) => palette.accent(text),
    bold: (text) => palette.bold(text),
    italic: (text) => palette.italic(text),
    strikethrough: (text) => palette.strike(text),
    underline: (text) => palette.underline(text)
  };
}
function selectTheme(palette) {
  return {
    selectedPrefix: palette.accent,
    selectedText: palette.accent,
    description: palette.dim,
    scrollInfo: palette.dim,
    noMatch: palette.warning
  };
}

// upstream/components/text.ts
var TERMINAL_CONTROL_PATTERN = /[\u0000-\u0009\u000b-\u001f\u007f-\u009f]/gu;
function displayText(text) {
  return text.replace(TERMINAL_CONTROL_PATTERN, (control) => `\\x${control.charCodeAt(0).toString(16).padStart(2, "0")}`);
}
function displayInlineText(text) {
  return displayText(text).replaceAll("\n", "\\x0a");
}

// upstream/components/transcript.ts
import {
  Container,
  Markdown,
  Spacer,
  Text,
  truncateToWidth,
  wrapTextWithAnsi
} from "@earendil-works/pi-tui";
import { diffLines as compareLines } from "diff";

// upstream/components/xml-tool-output.ts
import { SaxesParser } from "saxes";
function parseXml(source, display) {
  const parser = new SaxesParser({ xmlns: false });
  const stack = [];
  let root;
  const state = { invalid: false };
  const reject = () => {
    state.invalid = true;
  };
  parser.on("opentag", (tag) => {
    const element = {
      name: tag.name,
      // Attribute values and text pass through `display` because character references can
      // expand to valid-XML control characters (tab, CR, DEL, C1) that pre-parse escaping
      // of the raw source never saw. Element names cannot carry them: control characters
      // are not XML name characters and character references do not apply inside names.
      attributes: Object.entries(tag.attributes).map(([name, value]) => ({ name, value: display(value) })),
      children: []
    };
    const parent = stack.at(-1);
    if (parent === void 0) {
      if (root !== void 0) reject();
      root = element;
    } else {
      parent.children.push(element);
    }
    stack.push(element);
  });
  parser.on("text", (text) => {
    const parent = stack.at(-1);
    if (parent === void 0) {
      if (text.trim() !== "") reject();
    } else {
      parent.children.push(display(text));
    }
  });
  parser.on("cdata", (text) => {
    const parent = stack.at(-1);
    if (parent === void 0) reject();
    else parent.children.push(display(text));
  });
  parser.on("closetag", () => {
    stack.pop();
  });
  parser.on("xmldecl", reject);
  parser.on("processinginstruction", reject);
  parser.on("doctype", reject);
  parser.on("comment", reject);
  parser.on("error", reject);
  parser.write(source).close();
  return state.invalid ? void 0 : root;
}
function elementLabel(element) {
  const attributes = element.attributes.map((attribute) => `${attribute.name}=${JSON.stringify(attribute.value)}`).join(" ");
  return attributes === "" ? element.name : `${element.name} (${attributes})`;
}
function meaningfulChildren(element) {
  return element.children.filter((child) => typeof child !== "string" || child.trim() !== "");
}
function textBlock(text, depth, body) {
  return text.replace(/^\n|\n$/gu, "").split("\n").map((line) => line === "" ? line : `${"  ".repeat(depth)}${body(line)}`);
}
function treeLines(element, depth, label, body) {
  const indent = "  ".repeat(depth);
  const children = meaningfulChildren(element);
  if (children.length === 0) return [`${indent}${label(elementLabel(element))}`];
  if (children.length === 1 && typeof children[0] === "string" && !children[0].includes("\n")) {
    return [`${indent}${label(`${elementLabel(element)}:`)} ${body(children[0].trim())}`];
  }
  const lines = [`${indent}${label(elementLabel(element))}`];
  for (const child of children) {
    if (typeof child === "string") lines.push(...textBlock(child, depth + 1, body));
    else lines.push(...treeLines(child, depth + 1, label, body));
  }
  return lines;
}
function preview(lines, limit, omitted) {
  if (lines.length <= limit) return [...lines];
  const head = Math.ceil(limit / 2);
  const tail = limit - head;
  return [...lines.slice(0, head), omitted(lines.length - limit), ...lines.slice(lines.length - tail)];
}
function renderUnknownXml(source, maxChildLines, expanded, display, label, body, omitted) {
  const root = parseXml(source, display);
  if (root === void 0) return void 0;
  const blocks = meaningfulChildren(root).map((child) => typeof child === "string" ? textBlock(child, 1, body) : treeLines(child, 1, label, body));
  const rootLine = label(elementLabel(root));
  if (expanded) return [rootLine, ...blocks.flat()];
  const previewed = blocks.map((block) => preview(block, maxChildLines, omitted));
  if (previewed.length <= maxChildLines) return [rootLine, ...previewed.flat()];
  const head = Math.ceil(maxChildLines / 2);
  const tail = maxChildLines - head;
  const hidden = blocks.slice(head, blocks.length - tail).reduce((total, block) => total + block.length, 0);
  return [
    rootLine,
    ...previewed.slice(0, head).flat(),
    omitted(hidden),
    ...previewed.slice(previewed.length - tail).flat()
  ];
}

// upstream/components/content.ts
function contentText(content) {
  const parts = [];
  for (const block of content) {
    switch (block.type) {
      case "text":
      case "reasoning":
        parts.push(block.text);
        break;
      case "tool-call":
        parts.push(`${block.name}(${block.arguments})`);
        break;
      case "tool-result":
        parts.push(contentText(block.content));
        break;
      default: {
        const rawType = block.type;
        parts.push(`[${typeof rawType === "string" ? rawType : "content"}]`);
        break;
      }
    }
  }
  return parts.join("");
}

// upstream/chat/timing.ts
var TIMING_BUCKET_LABELS = {
  ttft: "Model wait",
  thinking: "Thinking",
  responding: "Response",
  tools: "Tools"
};
var TIMING_BUCKETS = ["ttft", "thinking", "responding", "tools"];
function emptyTimingTotals() {
  return { ttft: 0, thinking: 0, responding: 0, tools: 0 };
}
function timingState(startedAt) {
  return {
    totals: emptyTimingTotals(),
    /* v8 ignore next -- production timing state always begins at a logged step timestamp. */
    active: startedAt === void 0 ? void 0 : { bucket: "ttft", since: startedAt }
  };
}
function closeTimingBucket(state, at) {
  if (state.active === void 0) return;
  state.totals[state.active.bucket] += Math.max(0, at - state.active.since);
  state.active = void 0;
}
function enterTimingBucket(state, bucket, at) {
  if (state.active?.bucket === bucket) return;
  closeTimingBucket(state, at);
  if (bucket !== void 0) state.active = { bucket, since: at };
}
function advanceStepTiming(state, event) {
  if (event.type === "assistant/chunk") {
    const chunk = event.data.chunk;
    if (state.active?.bucket === "ttft") enterTimingBucket(state, void 0, event.time);
    if (chunk.type === "reasoning-delta" || chunk.type === "block-start" && chunk.blockType === "reasoning") {
      enterTimingBucket(state, "thinking", event.time);
    } else if (chunk.type === "text-delta" || chunk.type === "block-start" && chunk.blockType === "text") {
      enterTimingBucket(state, "responding", event.time);
    }
  } else if (event.type === "tool/call") {
    enterTimingBucket(state, "tools", event.time);
  } else {
    closeTimingBucket(state, event.time);
  }
}
function timingTotalsAt(state, at) {
  const totals = { ...state.totals };
  if (state.active !== void 0 && at !== void 0) {
    totals[state.active.bucket] += Math.max(0, at - state.active.since);
  }
  return totals;
}
function stepKey(position) {
  return `${position.turn}:${position.step}`;
}
var StepTimingTracker = class {
  scanned = 0;
  steps = /* @__PURE__ */ new Map();
  /**
   * Advance over events appended since the previous query, then return one
   * step's accumulated per-phase timing up to clock `at`.
   * @param events - Current session event log (append-only).
   * @param position - Turn/step coordinates of the queried step.
   * @param at - Render clock to accumulate the open bucket up to.
   * @returns The step's per-phase totals; empty when the step never started.
   */
  totalsAt(events, position, at) {
    for (; this.scanned < events.length; this.scanned += 1) {
      const event = events[this.scanned];
      if (event.type === "step/start") {
        const key = stepKey(event.data);
        if (!this.steps.has(key)) this.steps.set(key, { ...timingState(event.time), closed: false });
      } else if (event.type === "assistant/chunk" || event.type === "tool/call" || event.type === "step/end") {
        const state2 = this.steps.get(stepKey(event.data));
        if (state2 !== void 0 && !state2.closed) {
          advanceStepTiming(state2, event);
          if (event.type === "step/end") state2.closed = true;
        }
      }
    }
    const state = this.steps.get(stepKey(position));
    return state === void 0 ? emptyTimingTotals() : timingTotalsAt(state, at);
  }
};
function formatStatusDuration(elapsedMs) {
  const tenths = Math.floor(Math.max(0, elapsedMs) / 100);
  const seconds = tenths / 10;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m${(seconds - minutes * 60).toFixed(1).padStart(4, "0")}s`;
}
function formatTimingTotals(totals, includeModelWait = false) {
  return TIMING_BUCKETS.filter((bucket) => totals[bucket] > 0 || includeModelWait && bucket === "ttft").map((bucket) => `${TIMING_BUCKET_LABELS[bucket]} ${formatStatusDuration(totals[bucket])}`).join(" \xB7 ");
}
function formatCompletionTime(time) {
  const date = new Date(time);
  const parts = [
    date.getFullYear().toString().padStart(4, "0"),
    (date.getMonth() + 1).toString().padStart(2, "0"),
    date.getDate().toString().padStart(2, "0")
  ];
  const clock = [date.getHours(), date.getMinutes(), date.getSeconds()].map((value) => value.toString().padStart(2, "0")).join(":");
  return `${parts.join("-")} ${clock}`;
}

// upstream/components/transcript.ts
function textBlocks(content, type) {
  return content.filter((block) => block.type === type).map((block) => block.text).join("\n\n");
}
function pretty(value) {
  if (typeof value === "string") return displayText(value);
  const serialized = JSON.stringify(value, null, 2);
  return displayText(serialized ?? String(value));
}
function diffContentLines(text) {
  if (text === "") return [];
  const body = text.endsWith("\n") ? text.slice(0, -1) : text;
  return body.split("\n");
}
function renderDiff(diff, maxDiffEditLength, palette) {
  const lines = [palette.bold(displayText(diff.path))];
  let added = 0;
  let removed = 0;
  if (diff.oldText === null) {
    const newLines = diffContentLines(displayText(diff.newText));
    added = newLines.length;
    for (const line of newLines) lines.push(palette.success(`+ ${line}`));
    return { lines, added, removed, approximate: false };
  }
  const changes = compareLines(diff.oldText, diff.newText, { maxEditLength: maxDiffEditLength });
  if (changes === void 0) {
    const oldLines = diffContentLines(displayText(diff.oldText));
    const newLines = diffContentLines(displayText(diff.newText));
    lines.push(palette.dim(`[exact line diff omitted: >${maxDiffEditLength} changed lines]`));
    removed = oldLines.length;
    added = newLines.length;
    for (const line of oldLines) lines.push(palette.error(`- ${line}`));
    for (const line of newLines) lines.push(palette.success(`+ ${line}`));
    return { lines, added, removed, approximate: true };
  }
  for (const change of changes) {
    const changedLines = diffContentLines(displayText(change.value));
    if (change.added) {
      added += changedLines.length;
      for (const line of changedLines) lines.push(palette.success(`+ ${line}`));
    } else if (change.removed) {
      removed += changedLines.length;
      for (const line of changedLines) lines.push(palette.error(`- ${line}`));
    } else {
      for (const line of changedLines) lines.push(palette.dim(`  ${line}`));
    }
  }
  return { lines, added, removed, approximate: false };
}
function messageHeader(label, color, palette) {
  return palette.bold(palette.underline(color(displayText(label))));
}
var UserMessageComponent = class extends Container {
  constructor(text, palette, mdTheme, label = "You") {
    super();
    this.addChild(new Text(messageHeader(label, palette.accent, palette), 0, 0));
    this.addChild(new Markdown(displayText(text), 0, 0, mdTheme, { color: (value) => palette.text(value) }, {
      preserveOrderedListMarkers: true,
      preserveBackslashEscapes: true
    }));
  }
};
function assistantMessageChildren(content, showReasoning, foldedContinuation, palette, mdTheme) {
  const reasoning = displayText(textBlocks(content, "reasoning").trim());
  const text = displayText(textBlocks(content, "text").trim());
  const showsReasoning = reasoning !== "" && showReasoning;
  if (foldedContinuation && !showsReasoning && text === "") return [];
  const children = [new Spacer(1)];
  if (!foldedContinuation) {
    children.push(new Text(messageHeader("Assistant", palette.accent, palette), 0, 0));
  }
  if (showsReasoning) {
    children.push(
      new Text(palette.italic(palette.dim("Reasoning")), 0, 0),
      new Markdown(reasoning, 0, 0, mdTheme, { color: (value) => palette.dim(value), italic: true })
    );
  }
  if (text) children.push(new Markdown(text, 0, 0, mdTheme, { color: (value) => palette.text(value) }));
  return children;
}
var StepTimingComponent = class extends Container {
  constructor(position, events, tracker, now, palette) {
    super();
    this.position = position;
    this.events = events;
    this.tracker = tracker;
    this.now = now;
    this.palette = palette;
    this.rebuild();
  }
  completionTime;
  complete(time) {
    this.completionTime = time;
    this.rebuild();
  }
  invalidate() {
    this.rebuild();
    super.invalidate();
  }
  rebuild() {
    this.clear();
    const totals = this.tracker.totalsAt(this.events(), this.position, this.completionTime ?? this.now());
    const timing = formatTimingTotals(totals, true);
    const header = this.completionTime === void 0 ? timing : `${timing} \xB7 Completed ${formatCompletionTime(this.completionTime)}`;
    this.addChild(new Text(this.palette.dim(header), 0, 0));
  }
};
var StreamingAssistantComponent = class extends Container {
  constructor(position, events, tracker, now, showReasoning, palette, mdTheme) {
    super();
    this.position = position;
    this.showReasoning = showReasoning;
    this.palette = palette;
    this.mdTheme = mdTheme;
    this.timing = new StepTimingComponent(position, events, tracker, now, palette);
    this.rebuild();
  }
  blocks = /* @__PURE__ */ new Map();
  settledContent;
  foldedContinuation = false;
  /**
   * The step's timing footer. The renderer keeps it at the tail of the chat so
   * it trails any tool cards the step appends after this assistant message; it
   * is not a child of this component.
   */
  timing;
  /**
   * Replace the streamed blocks with the step's settled content.
   * @param content - The settled assistant content blocks.
   */
  settle(content) {
    this.settledContent = content;
    this.rebuild();
  }
  /**
   * Whether this step's assistant message has settled.
   * @returns `true` once {@link settle} has run.
   */
  isSettled() {
    return this.settledContent !== void 0;
  }
  /**
   * Pin the step's timing footer to its completion time.
   * @param time - Step completion time in epoch milliseconds.
   */
  complete(time) {
    this.timing.complete(time);
  }
  invalidate() {
    this.rebuild();
    this.timing.invalidate();
    super.invalidate();
  }
  /**
   * Fold one streamed chunk into the live block buffer and re-render.
   * @param chunk - The streamed assistant chunk.
   */
  update(chunk) {
    if (chunk.type === "block-start") {
      this.blocks.set(chunk.index, { type: chunk.blockType, text: "" });
    } else if (chunk.type === "text-delta" || chunk.type === "reasoning-delta") {
      const type = chunk.type === "text-delta" ? "text" : "reasoning";
      const block = this.blocks.get(chunk.index) ?? { type, text: "" };
      block.text += chunk.text;
      this.blocks.set(chunk.index, block);
    } else if (chunk.type === "block-end" && (chunk.block.type === "text" || chunk.block.type === "reasoning")) {
      this.blocks.set(chunk.index, { type: chunk.block.type, text: chunk.block.text });
    }
    this.rebuild();
    this.timing.invalidate();
  }
  /**
   * Toggle whether reasoning blocks render, then re-render.
   * @param show - Whether to show reasoning blocks.
   */
  setShowReasoning(show) {
    this.showReasoning = show;
    this.rebuild();
  }
  /**
   * Mark this step as a folded continuation of its turn: no `Assistant` header,
   * and no output at all while the step has no visible body. Used while tool
   * cards are hidden so a turn reads as one assistant message.
   * @param folded - Whether to render as a headerless continuation.
   */
  setFoldedContinuation(folded) {
    if (this.foldedContinuation === folded) return;
    this.foldedContinuation = folded;
    this.rebuild();
  }
  /**
   * Whether the step currently renders visible reasoning or text.
   * @returns `true` when a header-owning render would show a body.
   */
  hasVisibleBody() {
    const content = this.presentedContent();
    return textBlocks(content, "text").trim() !== "" || this.showReasoning && textBlocks(content, "reasoning").trim() !== "";
  }
  /** The settled content when available, otherwise the streamed blocks in model order. */
  presentedContent() {
    return this.settledContent ?? [...this.blocks.entries()].sort(([left], [right]) => left - right).flatMap(([, block]) => {
      if (block.type === "text") return [{ type: "text", text: block.text }];
      if (block.type === "reasoning") return [{ type: "reasoning", text: block.text }];
      return [];
    });
  }
  rebuild() {
    this.clear();
    const children = assistantMessageChildren(
      this.presentedContent(),
      this.showReasoning,
      this.foldedContinuation,
      this.palette,
      this.mdTheme
    );
    for (const child of children) this.addChild(child);
  }
};
var CachedCardComponent = class {
  cached;
  /** Discard the cached rows so the next render recomputes them. */
  dropLines() {
    this.cached = void 0;
  }
  invalidate() {
    this.cached = void 0;
  }
  render(width) {
    if (this.cached?.width !== width) this.cached = { width, lines: this.renderLines(width) };
    return this.cached.lines;
  }
};
var ToolCardComponent = class extends CachedCardComponent {
  constructor(name, parsed, definition, maxOutputLines, maxDiffEditLength, palette, mdTheme) {
    super();
    this.name = name;
    this.parsed = parsed;
    this.definition = definition;
    this.maxOutputLines = maxOutputLines;
    this.maxDiffEditLength = maxDiffEditLength;
    this.palette = palette;
    this.mdTheme = mdTheme;
    this.callView = this.presentCall();
  }
  result;
  visibility = "collapsed";
  callView;
  resultView;
  diffBodyCache;
  presentCall() {
    if (this.parsed.valid && this.definition?.presentCall) {
      try {
        const view = this.definition.presentCall(this.parsed.value);
        if (view !== void 0) return view;
      } catch (error) {
        return { card: "generic", title: displayText(this.name), rawInput: `Presenter failed: ${String(error)}` };
      }
    }
    return { card: "generic", title: displayText(this.name), rawInput: this.parsed.value };
  }
  /**
   * Record the tool result and derive its result view.
   * @param event - The `tool/result` event payload.
   */
  updateResult(event) {
    this.diffBodyCache = void 0;
    this.dropLines();
    const result = event.message.content[0];
    this.result = {
      content: [...result.content],
      isError: result.isError === true,
      ...event.meta !== void 0 ? { meta: event.meta } : {}
    };
    if (this.parsed.valid && this.definition?.presentResult) {
      try {
        const view = this.definition.presentResult(this.parsed.value, this.result);
        if (view !== void 0) this.resultView = view;
      } catch (error) {
        this.resultView = { card: "generic", content: [{ type: "text", text: `Presenter failed: ${String(error)}` }] };
      }
    }
  }
  /**
   * Set the card's visibility state.
   * @param visibility - Hidden, collapsed preview, or full body.
   */
  setVisibility(visibility) {
    this.visibility = visibility;
    this.dropLines();
  }
  renderLines(width) {
    if (this.visibility === "hidden") return [];
    const isError = this.result?.isError ?? false;
    const glyph = this.result === void 0 ? "\u25CB" : "\u25CF";
    const rawBody = this.renderBody();
    const view = this.resultView ?? this.callView;
    const markdownContent = view.card === "generic" || view.card === "read" ? view.content ?? this.result?.content : view.card === "search" ? this.result?.content : view.card === "web" ? this.result?.content : void 0;
    const unknownXml = this.definition === void 0 && markdownContent !== void 0 ? renderUnknownXml(
      displayText(contentText(markdownContent)),
      this.maxOutputLines,
      this.visibility === "expanded",
      displayText,
      (text) => this.palette.dim(text),
      (text) => this.palette.dim(text),
      /* v8 ignore next -- renderUnknownXml calls the collapsed summary only when hidden XML children exceed this card's limit. */
      (count) => this.palette.dim(`  \u2026 +${count} lines (Ctrl+O to expand)`)
    ) : void 0;
    const body = unknownXml ?? (markdownContent !== void 0 && rawBody.lines.length > 0 ? this.dimBody(rawBody, width) : [...rawBody.prelude, ...rawBody.lines]);
    const visibleBody = unknownXml !== void 0 || this.visibility === "expanded" ? body : preview(body, this.maxOutputLines, (count) => this.palette.dim(`\u2026 +${count} lines (Ctrl+O to expand)`));
    const statusColor = this.result === void 0 ? this.palette.warning : isError ? this.palette.error : this.palette.success;
    const desc = this.headerDescription();
    const headerText = `${glyph} Tool / ${displayText(this.name)}${desc === void 0 ? "" : ` / ${displayInlineText(desc)}`}`;
    const header = truncateToWidth(headerText, Math.max(1, width - 2), "");
    const lines = ["", statusColor(header)];
    if (visibleBody.length > 0) lines.push(...new Text(visibleBody.join("\n"), 0, 0).render(width));
    return lines;
  }
  /** The pending terminal call view, when this row is a terminal card. */
  terminalPending() {
    return this.callView.card === "terminal" ? this.callView : void 0;
  }
  /**
   * The optional header `/ <desc>` segment: a bash (terminal) card's
   * model-authored description. Non-terminal tools contribute no header detail —
   * their presenter title moves into the body instead.
   */
  headerDescription() {
    const description = this.terminalPending()?.description;
    return description !== void 0 && description !== "" ? description : void 0;
  }
  /**
   * The presenter's title for a non-terminal card, shown as the first body line
   * (a read's `Read src/foo.ts`, a diff's `Edit files`) now that the header is a
   * fixed `Tool / <name>` frame. The result-state title replaces the pending one.
   */
  bodyTitle() {
    return this.resultView?.title ?? this.callView.title;
  }
  renderBody() {
    const view = this.resultView ?? this.callView;
    if (view.card === "terminal") {
      const pending = this.terminalPending();
      const prelude2 = [];
      const lines2 = [];
      const headlined = pending?.description !== void 0 && pending.description !== "";
      const commandInBody = pending !== void 0 && (headlined || this.result === void 0);
      if (commandInBody) prelude2.push(this.palette.dim(`$ ${displayInlineText(pending.title)}`));
      if (pending?.cwd) prelude2.push(this.palette.dim(displayInlineText(pending.cwd)));
      if (this.resultView?.card === "terminal") {
        if (this.resultView.output) lines2.push(...this.dimOutput(this.resultView.output));
        if (this.resultView.exitCode !== void 0) lines2.push(this.palette.dim(`[exit ${this.resultView.exitCode}]`));
        if (this.resultView.signal !== void 0) {
          lines2.push(this.palette.error(`[signal ${displayText(this.resultView.signal)}]`));
        }
      } else if (this.result !== void 0) {
        lines2.push(...this.dimOutput(contentText(this.result.content)));
      }
      return { prelude: prelude2.filter(Boolean), lines: lines2.filter(Boolean) };
    }
    if (view.card === "diff") {
      if (this.diffBodyCache?.view === view) return this.diffBodyCache.body;
      const renderedDiffs = view.diffs.map(
        (diff) => renderDiff(diff, this.maxDiffEditLength, this.palette)
      );
      const added = renderedDiffs.reduce((total2, rendered) => total2 + rendered.added, 0);
      const removed = renderedDiffs.reduce((total2, rendered) => total2 + rendered.removed, 0);
      const approximate = renderedDiffs.some((rendered) => rendered.approximate);
      const hunks = renderedDiffs.flatMap((rendered, index) => {
        return [...index > 0 ? [""] : [], ...rendered.lines];
      });
      const files = new Set(view.diffs.map((diff) => diff.path)).size;
      const footer = this.palette.dim(
        `\u2514 +${added} -${removed} \xB7 ${files} file${files === 1 ? "" : "s"}${approximate ? " \xB7 approximate" : ""}`
      );
      const body = { prelude: [...hunks, footer], lines: [] };
      this.diffBodyCache = { view, body };
      return body;
    }
    const content = (view.card === "generic" || view.card === "read" ? view.content : void 0) ?? this.result?.content;
    const prelude = [];
    const lines = [];
    const bodyTitle = this.bodyTitle();
    if (bodyTitle !== displayText(this.name)) prelude.push(displayInlineText(bodyTitle));
    if (content !== void 0) lines.push(...displayText(contentText(content)).split("\n"));
    const rawInput = this.result === void 0 && this.callView.card === "generic" ? this.callView.rawInput : void 0;
    if (rawInput !== void 0) lines.push(...pretty(rawInput).split("\n"));
    const total = prelude.length + lines.length;
    return {
      prelude,
      lines: lines.filter((line, index) => {
        const row = prelude.length + index;
        return line.length > 0 || row > 0 && row < total - 1;
      })
    };
  }
  /**
   * A tool's own output text as dim rows — the card's result-output color, which
   * separates what the tool produced from the card's own framing. A blank row
   * stays the empty string so the terminal branch's blank-row filter still reads
   * it as blank instead of as an ANSI-wrapped value.
   */
  dimOutput(text) {
    return displayText(text).split("\n").map((line) => line === "" ? line : this.palette.dim(line));
  }
  /**
   * Render a generic card's prelude and result as one Markdown document under the
   * dim body tone. Rendering both together preserves the document's own block
   * spacing (Markdown's blank row before a heading); dimming every row keeps the
   * card body one uniform tone, so only the status-colored header carries color.
   */
  dimBody(body, width) {
    const rows = new Markdown([...body.prelude, ...body.lines].join("\n"), 0, 0, this.mdTheme, {
      color: (value) => this.palette.text(value)
    }).render(width);
    return rows.map((row) => row.trim() === "" ? row : this.palette.dim(row));
  }
};

// upstream/components/dialogs.ts
import {
  Input,
  Key,
  SelectList,
  matchesKey,
  truncateToWidth as truncateToWidth2,
  visibleWidth,
  wrapTextWithAnsi as wrapTextWithAnsi2
} from "@earendil-works/pi-tui";
var QuestionDialog = class {
  constructor(question, position, total, unanswered, maxVisible, maxHeight, palette, done, cancel) {
    this.question = question;
    this.position = position;
    this.total = total;
    this.unanswered = unanswered;
    this.maxVisible = maxVisible;
    this.maxHeight = maxHeight;
    this.palette = palette;
    this.done = done;
    this.cancel = cancel;
    this.options = question.options ?? [];
    this.mode = this.options.length > 0 ? "options" : "custom";
    this.input.onSubmit = (value) => {
      this.submitCustom(value);
    };
    this.input.onEscape = () => {
      if (this.options.length > 0) {
        this.mode = "options";
        this.error = "";
      } else {
        this.cancel();
      }
    };
  }
  selectedIndex = 0;
  selected = /* @__PURE__ */ new Set();
  headerPage = { offset: 0, size: 1, maxOffset: 0 };
  selectedBlockPage = { offset: 0, size: 1, maxOffset: 0 };
  mode;
  error = "";
  input = new Input();
  options;
  focused = false;
  invalidate() {
    this.input.invalidate();
  }
  handleInput(data) {
    this.invalidate();
    if (matchesKey(data, Key.pageUp)) {
      this.pageBackward();
      return;
    }
    if (matchesKey(data, Key.pageDown)) {
      this.pageForward();
      return;
    }
    if (this.mode === "custom") {
      this.input.focused = this.focused;
      this.input.handleInput(data);
      return;
    }
    const options = this.options;
    if (matchesKey(data, Key.up)) {
      this.selectedBlockPage = { offset: 0, size: 1, maxOffset: 0 };
      this.selectedIndex = this.selectedIndex === 0 ? options.length - 1 : this.selectedIndex - 1;
    } else if (matchesKey(data, Key.down)) {
      this.selectedBlockPage = { offset: 0, size: 1, maxOffset: 0 };
      this.selectedIndex = this.selectedIndex === options.length - 1 ? 0 : this.selectedIndex + 1;
    } else if (matchesKey(data, Key.space) && this.question.multiSelect) {
      if (this.selected.has(this.selectedIndex)) this.selected.delete(this.selectedIndex);
      else this.selected.add(this.selectedIndex);
    } else if (matchesKey(data, Key.enter)) {
      const selected = this.question.multiSelect ? this.selectedOptionLabels() : [options[this.selectedIndex]?.label].filter((label) => label !== void 0);
      const custom = this.question.multiSelect ? this.input.getValue().trim() : "";
      if (selected.length === 0 && custom === "") {
        this.error = "Select at least one option, or press Tab for a custom answer.";
        return;
      }
      this.done({ selected, ...custom === "" ? {} : { custom } });
    } else if (matchesKey(data, Key.tab) || data.toLowerCase() === "c") {
      this.mode = "custom";
      this.selectedBlockPage = { offset: 0, size: 1, maxOffset: 0 };
      this.error = "";
    } else if (matchesKey(data, Key.escape) || matchesKey(data, Key.ctrl("c"))) {
      this.cancel();
    }
  }
  submitCustom(value) {
    const custom = value.trim();
    if (custom === "") {
      this.error = "Enter an answer before submitting.";
      return;
    }
    this.done({
      selected: this.question.multiSelect ? this.selectedOptionLabels() : [],
      custom
    });
  }
  selectedOptionLabels() {
    return [...this.selected].sort((a, b) => a - b).map((index) => this.options[index]?.label).filter((label) => label !== void 0);
  }
  /** Page backward through an oversized option, then through question detail. */
  pageBackward() {
    if (this.mode === "options" && this.selectedBlockPage.offset > 0) {
      this.selectedBlockPage = {
        ...this.selectedBlockPage,
        offset: Math.max(0, this.selectedBlockPage.offset - this.selectedBlockPage.size)
      };
      return;
    }
    this.headerPage = {
      ...this.headerPage,
      offset: Math.max(0, this.headerPage.offset - this.headerPage.size)
    };
  }
  /** Page forward through question detail, then through an oversized option. */
  pageForward() {
    if (this.headerPage.offset < this.headerPage.maxOffset) {
      this.headerPage = {
        ...this.headerPage,
        offset: Math.min(
          this.headerPage.maxOffset,
          this.headerPage.offset + this.headerPage.size
        )
      };
      return;
    }
    if (this.mode === "custom") return;
    this.selectedBlockPage = {
      ...this.selectedBlockPage,
      offset: Math.min(
        this.selectedBlockPage.maxOffset,
        this.selectedBlockPage.offset + this.selectedBlockPage.size
      )
    };
  }
  render(width) {
    this.input.focused = this.focused;
    const horizontalPadding = Math.min(2, Math.max(0, Math.floor((width - 1) / 2)));
    const innerWidth = Math.max(1, width - horizontalPadding * 2);
    const header = `Question ${this.position}/${this.total} (${this.unanswered} unanswered)${this.question.header === void 0 ? "" : ` \xB7 ${displayText(this.question.header)}`}`;
    const questionLines = wrapTextWithAnsi2(
      this.palette.text(displayText(this.question.question)),
      innerWidth
    );
    const contentLines = [...questionLines];
    const headerLines = [
      ...wrapTextWithAnsi2(this.palette.dim(header), innerWidth),
      ...questionLines
    ];
    if (this.question.detail !== void 0) {
      headerLines.push("");
      contentLines.push("");
      for (const line of wrapTextWithAnsi2(displayText(this.question.detail), innerWidth)) {
        headerLines.push(line);
        contentLines.push(line);
      }
    }
    headerLines.push("");
    const customControls = [
      ...this.options.length > 0 && this.question.multiSelect ? [`${this.selected.size} selected`] : [],
      "Enter submit",
      this.options.length > 0 ? "Esc options" : "Esc cancel"
    ];
    const customHint = this.palette.dim(customControls.join(" \u2022 "));
    const footerLines = [];
    if (this.mode === "custom") {
      for (const line of this.input.render(innerWidth)) footerLines.push(line);
      for (const line of wrapTextWithAnsi2(customHint, innerWidth)) footerLines.push(line);
    } else {
      const controls = [
        "Tab custom answer",
        ...this.options.length > 1 ? ["\u2191/\u2193 navigate"] : [],
        ...this.question.multiSelect ? ["Space toggle"] : [],
        "Enter submit",
        "Esc interrupt"
      ];
      const hint = this.palette.dim(controls.join(" \u2022 "));
      for (const line of wrapTextWithAnsi2(hint, innerWidth)) footerLines.push(line);
    }
    if (this.error) {
      for (const line of wrapTextWithAnsi2(this.palette.error(this.error), innerWidth)) footerLines.push(line);
    }
    const positionLines = this.mode === "options" && this.options.length > this.maxVisible ? [this.palette.dim(`${this.selectedIndex + 1}/${this.options.length}`)] : [];
    const paddingRows = 2;
    const maxHeight = this.maxHeight();
    const availableForOptions = Math.max(
      this.mode === "options" ? 4 : 1,
      maxHeight - paddingRows - headerLines.length - positionLines.length - footerLines.length
    );
    const body = [...headerLines];
    const optionLines = [];
    if (this.mode === "custom") {
      for (const line of footerLines) body.push(line);
    } else {
      const optionBlocks = this.options.map((option, index) => this.renderOptionBlock(option, index, innerWidth));
      const { visibleBlocks, hiddenBefore, hiddenAfter } = this.windowBlocks(optionBlocks, availableForOptions, innerWidth);
      if (hiddenBefore > 0) optionLines.push(this.palette.dim(`\u2191 ${hiddenBefore} more`));
      for (const block of visibleBlocks) {
        for (const line of block) optionLines.push(line);
      }
      if (hiddenAfter > 0) optionLines.push(this.palette.dim(`\u2193 ${hiddenAfter} more`));
      for (const line of optionLines) body.push(line);
      for (const line of positionLines) body.push(line);
      for (const line of footerLines) body.push(line);
    }
    const rows = ["", ...body, ""];
    let visibleRows = rows;
    if (rows.length <= maxHeight) this.headerPage = { offset: 0, size: 1, maxOffset: 0 };
    if (rows.length > maxHeight && this.mode === "options" && maxHeight >= 6) {
      const headerBudget = Math.max(
        0,
        maxHeight - optionLines.length - (this.error === "" ? 1 : 2)
      );
      const compactFooter = [
        ...this.error === "" ? [] : [truncateToWidth2(this.palette.error(`Error: ${this.error}`), innerWidth, "\u2026")],
        this.compactOptionControls(
          innerWidth,
          headerBudget === 1 && contentLines.length > headerBudget
        )
      ];
      const compactHeader = this.compactQuestionHeader(contentLines, headerBudget, innerWidth);
      visibleRows = [...compactHeader, ...optionLines, ...compactFooter];
    } else if (rows.length > maxHeight && this.mode === "custom" && maxHeight >= 2) {
      const compactFooterSource = [
        ...this.input.render(innerWidth),
        this.compactCustomControls(innerWidth),
        ...this.error === "" ? [] : [truncateToWidth2(this.palette.error(this.error), innerWidth, "\u2026")]
      ];
      const footerBudget = Math.max(1, maxHeight - 1);
      const compactFooter = compactFooterSource.length <= footerBudget ? compactFooterSource : footerBudget === 1 ? compactFooterSource.slice(0, 1) : [
        ...compactFooterSource.slice(0, 1),
        ...compactFooterSource.slice(-(footerBudget - 1))
      ];
      const compactHeader = this.compactQuestionHeader(
        contentLines,
        Math.max(0, maxHeight - compactFooter.length),
        innerWidth
      );
      visibleRows = [...compactHeader, ...compactFooter];
    }
    if (visibleRows.length > maxHeight) {
      visibleRows = maxHeight === 1 ? [this.palette.dim(`\u2191 ${visibleRows.length} lines hidden`)] : [
        this.palette.dim(`\u2191 ${visibleRows.length - maxHeight + 1} lines hidden`),
        ...visibleRows.slice(-(maxHeight - 1))
      ];
    }
    return visibleRows.map((line) => {
      const bounded = truncateToWidth2(line, innerWidth, "\u2026");
      const pad = " ".repeat(Math.max(0, innerWidth - visibleWidth(bounded)));
      const outerPad = " ".repeat(horizontalPadding);
      return `${outerPad}${bounded}${pad}${outerPad}`;
    });
  }
  /** Render one option as wrapped label and indented description lines. */
  renderOptionBlock(option, index, innerWidth) {
    const cursor = index === this.selectedIndex ? "\u203A" : " ";
    const number = `${index + 1}. `;
    const mark = this.question.multiSelect ? this.selected.has(index) ? "[x] " : "[ ] " : "";
    const labelPrefixPlain = ` ${cursor} ${number}${mark}`;
    const labelPrefixWidth = visibleWidth(labelPrefixPlain);
    const labelBodyWidth = Math.max(1, innerWidth - labelPrefixWidth);
    const labelLines = wrapTextWithAnsi2(displayText(option.label), labelBodyWidth);
    const continuation = " ".repeat(labelPrefixWidth);
    const lines = [];
    for (const [lineIndex, labelLine] of labelLines.entries()) {
      const prefix = lineIndex === 0 ? labelPrefixPlain : continuation;
      const composed = `${prefix}${labelLine}`;
      lines.push(index === this.selectedIndex ? this.palette.bold(this.palette.accent(composed)) : composed);
    }
    if (option.description !== void 0) {
      const descIndent = " ".repeat(labelPrefixWidth);
      const descBodyWidth = Math.max(1, innerWidth - labelPrefixWidth);
      const descLines = wrapTextWithAnsi2(displayText(option.description), descBodyWidth);
      for (const descLine of descLines) lines.push(`${descIndent}${this.palette.dim(descLine)}`);
    }
    return lines;
  }
  /** Keep the question visible when fixed chrome must be compacted. */
  compactQuestionHeader(contentLines, budget, innerWidth) {
    if (budget <= 0) return [];
    if (contentLines.length <= budget) {
      this.headerPage = { offset: 0, size: 1, maxOffset: 0 };
      return [...contentLines];
    }
    const pageSize = Math.max(1, budget - 1);
    const maxOffset = Math.max(0, contentLines.length - pageSize);
    const offset = Math.min(this.headerPage.offset, maxOffset);
    this.headerPage = { offset, size: pageSize, maxOffset };
    const keptLines = contentLines.slice(offset, offset + pageSize);
    if (budget === 1) {
      return [keptLines[0]];
    }
    return [
      ...keptLines,
      this.pagerStatus(offset + 1, offset + keptLines.length, contentLines.length, innerWidth)
    ];
  }
  /** Keep Page Up / Page Down discoverable when a full pager status cannot fit. */
  pagerStatus(first, last, total, innerWidth) {
    const full = `\u2026 lines ${first}-${last}/${total} \u2022 PgUp/PgDn`;
    const compact = `PgUp/PgDn ${first}/${total}`;
    return this.palette.dim(truncateToWidth2(
      visibleWidth(full) <= innerWidth ? full : compact,
      innerWidth,
      "\u2026"
    ));
  }
  /** Render custom-mode controls on one row when the header must compact. */
  compactCustomControls(innerWidth) {
    const controls = this.options.length > 0 ? "Enter submit \u2022 Esc options" : "Enter submit \u2022 Esc cancel";
    const fallback = this.options.length > 0 ? "\u21B5 Esc options" : "Enter Esc cancel";
    const line = visibleWidth(controls) <= innerWidth ? controls : fallback;
    return this.palette.dim(truncateToWidth2(line, innerWidth, "\u2026"));
  }
  /** Render a one-row option footer that retains every mode-specific control. */
  compactOptionControls(innerWidth, showPager = false) {
    const controls = [
      ...this.options.length > 1 ? ["\u2191/\u2193"] : [],
      "Tab custom",
      ...this.question.multiSelect ? ["Space toggle"] : [],
      "Enter",
      "Esc interrupt",
      ...showPager ? ["PgUp/PgDn"] : []
    ].join(" \u2022 ");
    const optionNavigation = this.options.length > 1 ? "\u2191\u2193 " : "";
    const fallback = showPager ? `P\u2191\u2193 ${optionNavigation}Tab${this.question.multiSelect ? " S" : ""}\u21B5Esc` : this.question.multiSelect ? `${optionNavigation}Tab Sp \u21B5Esc` : `${optionNavigation}Tab \u21B5 Esc`;
    const line = visibleWidth(controls) <= innerWidth ? controls : fallback;
    return this.palette.dim(truncateToWidth2(line, innerWidth, "\u2026"));
  }
  /**
   * Choose option blocks that fit while keeping the selected option visible.
   * Omitted blocks are counted at each end for explicit overflow markers.
   */
  windowBlocks(blocks, budget, innerWidth) {
    const totalLines = blocks.reduce((sum, block) => sum + block.length, 0);
    if (totalLines <= budget && blocks.length <= this.maxVisible) {
      return { visibleBlocks: [...blocks], hiddenBefore: 0, hiddenAfter: 0 };
    }
    let start = this.selectedIndex;
    let end = this.selectedIndex + 1;
    let used = blocks[this.selectedIndex]?.length ?? 0;
    const markerLines = (before, after) => (before > 0 ? 1 : 0) + (after > 0 ? 1 : 0);
    const fits = (nextStart, nextEnd, nextUsed) => nextEnd - nextStart <= this.maxVisible && nextUsed + markerLines(nextStart, blocks.length - nextEnd) <= budget;
    const selectedMarkers = markerLines(start, blocks.length - end);
    if (used + selectedMarkers > budget) {
      const selectedBlock = blocks[this.selectedIndex] ?? [];
      const hiddenBefore = start;
      const hiddenAfter = blocks.length - end;
      const pageSize = budget - selectedMarkers - 1;
      const maxOffset = Math.max(0, selectedBlock.length - pageSize);
      const offset = Math.min(this.selectedBlockPage.offset, maxOffset);
      this.selectedBlockPage = { offset, size: pageSize, maxOffset };
      const keptLines = selectedBlock.slice(offset, offset + pageSize);
      const first = offset + 1;
      const last = offset + keptLines.length;
      const overflow = this.pagerStatus(first, last, selectedBlock.length, innerWidth);
      return {
        visibleBlocks: [[...keptLines, overflow]],
        hiddenBefore,
        hiddenAfter
      };
    }
    this.selectedBlockPage = { offset: 0, size: 1, maxOffset: 0 };
    let expanded = true;
    while (expanded && (start > 0 || end < blocks.length)) {
      expanded = false;
      if (end < blocks.length) {
        const next = blocks[end]?.length ?? 0;
        if (fits(start, end + 1, used + next)) {
          used += next;
          end += 1;
          expanded = true;
          continue;
        }
      }
      if (start > 0) {
        const previous = blocks[start - 1]?.length ?? 0;
        if (fits(start - 1, end, used + previous)) {
          used += previous;
          start -= 1;
          expanded = true;
        }
      }
    }
    return {
      visibleBlocks: blocks.slice(start, end),
      hiddenBefore: start,
      hiddenAfter: blocks.length - end
    };
  }
};
export {
  QuestionDialog,
  StepTimingTracker,
  StreamingAssistantComponent,
  ToolCardComponent,
  UserMessageComponent,
  createPalette,
  displayText,
  markdownTheme,
  selectTheme
};
