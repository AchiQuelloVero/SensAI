// builds DOM nodes (never innerHTML), so anything the model writes is shown as text

export function makeCopyButton(getText) {
  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "copy-btn";
  copy.textContent = "Copy";
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(getText());
      copy.textContent = "Copied";
    } catch (e) {
      copy.textContent = "Copy failed";
    }
    setTimeout(() => { copy.textContent = "Copy"; }, 1500);
  });
  return copy;
}

const INLINE_SOURCE = [
  "\\\\(?<esc>[\\\\`*_{}\\[\\]()#+\\-.!~|>])",
  "(?<tick>`+)(?<code>[\\s\\S]*?[^`])\\k<tick>(?!`)",
  "\\[(?<ltext>[^\\]\\n]+)\\]\\((?<url>[^)\\s]+)(?:\\s+\"[^\"]*\")?\\)",
  "\\*\\*(?=\\S)(?<b1>[\\s\\S]*?\\S)\\*\\*",
  "\\b__(?=\\S)(?<b2>[\\s\\S]*?\\S)__\\b",
  "~~(?=\\S)(?<del>[\\s\\S]*?\\S)~~",
  "\\*(?=[^\\s*])(?<e1>[\\s\\S]*?[^\\s*])\\*",
  "\\b_(?=[^\\s_])(?<e2>[\\s\\S]*?[^\\s_])_\\b",
  "(?<auto>https?:\\/\\/[^\\s<>]*[^\\s<>.,;:!?)\\]'\"])",
].join("|");

// `label` is formatted as inline markdown unless the link is a bare URL
function safeLink(label, url, parent, bare = false) {
  if (!/^(https?:|mailto:)/i.test(url)) {
    parent.append(label);
    return;
  }
  const a = document.createElement("a");
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  if (bare) a.textContent = label;
  else inline(label, a, true);
  parent.append(a);
}

function inline(text, parent, inLink = false) {
  const re = new RegExp(INLINE_SOURCE, "g"); // own regex per call, inline() recurses
  let last = 0, m;
  const wrap = (tag, inner) => {
    const el = document.createElement(tag);
    inline(inner, el, inLink);
    parent.append(el);
  };
  while ((m = re.exec(text))) {
    if (m.index > last) parent.append(text.slice(last, m.index));
    const g = m.groups;
    if (g.esc !== undefined) parent.append(g.esc);
    else if (g.code !== undefined) {
      const code = document.createElement("code");
      code.textContent = /^ .*[^ ].* $/.test(g.code) ? g.code.slice(1, -1) : g.code;
      parent.append(code);
    } else if (g.ltext !== undefined && !inLink) safeLink(g.ltext, g.url, parent);
    else if (g.b1 !== undefined || g.b2 !== undefined) wrap("strong", g.b1 ?? g.b2);
    else if (g.del !== undefined) wrap("del", g.del);
    else if (g.e1 !== undefined || g.e2 !== undefined) wrap("em", g.e1 ?? g.e2);
    else if (g.auto !== undefined && !inLink) safeLink(g.auto, g.auto, parent, true);
    else parent.append(m[0]); // a link inside a link label stays text
    last = re.lastIndex;
  }
  if (last < text.length) parent.append(text.slice(last));
}

function inlineLines(lines, parent) {
  lines.forEach((line, i) => {
    if (i) parent.append(document.createElement("br"));
    inline(line.trim(), parent);
  });
}

function codeBlock(lang, text) {
  const block = document.createElement("div");
  block.className = "code-block";
  const head = document.createElement("div");
  head.className = "code-head";
  head.append(Object.assign(document.createElement("span"), { textContent: lang || "code" }), makeCopyButton(() => text));
  const pre = document.createElement("pre");
  const code = document.createElement("code");
  code.textContent = text;
  pre.append(code);
  block.append(head, pre);
  return block;
}

const MD_FENCE = /^\s*(```+|~~~+)\s*([^\s`]*)\s*$/;
const MD_HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const MD_RULE = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
const MD_QUOTE = /^\s{0,3}>\s?(.*)$/;
const MD_ITEM = /^(\s*)([-*+]|(\d{1,9})[.)])\s+(.*)$/;
const MD_TABLE_SEP = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;

function splitRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}

function startsBlock(line) {
  return MD_FENCE.test(line) || MD_HEADING.test(line) || MD_RULE.test(line) || MD_QUOTE.test(line) || MD_ITEM.test(line);
}

function renderList(lines, start, parent) {
  const first = lines[start].match(MD_ITEM);
  const base = first[1].length;
  const ordered = first[3] !== undefined;
  const list = document.createElement(ordered ? "ol" : "ul");
  if (ordered && Number(first[3]) !== 1) list.start = Number(first[3]);
  let i = start;
  while (i < lines.length) {
    const m = lines[i].match(MD_ITEM);
    if (!m || m[1].length !== base || (m[3] !== undefined) !== ordered) break;
    const offset = m[0].length - m[4].length;
    const body = [m[4]];
    i++;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) {
        // a blank line stays inside the item only when indented content follows
        let j = i;
        while (j < lines.length && !lines[j].trim()) j++;
        if (j < lines.length && lines[j].match(/^\s*/)[0].length > base) { body.push(""); i++; continue; }
        break;
      }
      if (line.match(/^\s*/)[0].length <= base) break;
      body.push(line.slice(Math.min(line.match(/^\s*/)[0].length, offset)));
      i++;
    }
    const li = document.createElement("li");
    renderBlocks(body, li);
    const p = li.firstElementChild;
    if (p && p.tagName === "P" && p === li.firstChild) p.replaceWith(...p.childNodes); // tight list: no paragraph gap
    list.append(li);
    // blank lines between items of the same list
    let j = i;
    while (j < lines.length && !lines[j].trim()) j++;
    const next = j < lines.length && lines[j].match(MD_ITEM);
    if (next && next[1].length === base && (next[3] !== undefined) === ordered) i = j;
  }
  parent.append(list);
  return i;
}

function renderBlocks(lines, parent) {
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    let m;

    if ((m = line.match(MD_FENCE))) {
      const marker = m[1];
      const body = [];
      i++;
      while (i < lines.length) {
        const t = lines[i].trim();
        if (t[0] === marker[0] && t.length >= marker.length && /^(`+|~+)$/.test(t)) break;
        body.push(lines[i]);
        i++;
      }
      i++; // closing fence; a missing one (still streaming) just ends the block
      parent.append(codeBlock(m[2], body.join("\n")));
    } else if ((m = line.match(MD_HEADING))) {
      const h = document.createElement(`h${m[1].length}`);
      inline(m[2], h);
      parent.append(h);
      i++;
    } else if (MD_RULE.test(line)) {
      parent.append(document.createElement("hr"));
      i++;
    } else if (MD_QUOTE.test(line)) {
      const inner = [];
      while (i < lines.length && (m = lines[i].match(MD_QUOTE))) { inner.push(m[1]); i++; }
      const quote = document.createElement("blockquote");
      renderBlocks(inner, quote);
      parent.append(quote);
    } else if (line.includes("|") && i + 1 < lines.length && MD_TABLE_SEP.test(lines[i + 1]) && lines[i + 1].includes("-")) {
      const heads = splitRow(line);
      const aligns = splitRow(lines[i + 1]).map((c) => (c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : ""));
      i += 2;
      const table = document.createElement("table");
      const tr = table.createTHead().insertRow();
      heads.forEach((text, c) => {
        const th = document.createElement("th");
        th.style.textAlign = aligns[c] || "";
        inline(text, th);
        tr.append(th);
      });
      const tbody = table.createTBody();
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
        const row = tbody.insertRow();
        splitRow(lines[i]).forEach((text, c) => {
          const td = row.insertCell();
          td.style.textAlign = aligns[c] || "";
          inline(text, td);
        });
        i++;
      }
      const scroll = document.createElement("div");
      scroll.className = "table-wrap";
      scroll.append(table);
      parent.append(scroll);
    } else if (MD_ITEM.test(line)) {
      i = renderList(lines, i, parent);
    } else {
      const para = [];
      while (i < lines.length && lines[i].trim() && (para.length === 0 || !startsBlock(lines[i]))) {
        para.push(lines[i]);
        i++;
      }
      const p = document.createElement("p");
      inlineLines(para, p);
      parent.append(p);
    }
  }
}

export function renderMarkdown(src) {
  const fragment = document.createDocumentFragment();
  renderBlocks(src.replace(/\r\n?/g, "\n").replace(/^\t+/gm, (tabs) => "    ".repeat(tabs.length)).split("\n"), fragment);
  return fragment;
}
