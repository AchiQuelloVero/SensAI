import { api } from "./api.js";
import { closeSidebar } from "./dom.js";

const kbDialog = document.getElementById("kb-dialog");
const kbBody = document.getElementById("kb-body");

function modelControl(kb, suggestedModels) {
  const row = document.createElement("form");
  row.className = "kb-model";
  const label = Object.assign(document.createElement("span"), { className: "kb-model-label", textContent: "Embedding model" });
  const input = Object.assign(document.createElement("input"), { value: kb.embed_model, spellcheck: false });
  input.setAttribute("list", "kb-model-suggestions");
  input.setAttribute("aria-label", `Embedding model for ${kb.name}`);
  const submit = Object.assign(document.createElement("button"), { type: "submit", className: "settings-btn-secondary", textContent: "Change" });
  const error = Object.assign(document.createElement("div"), { className: "perm-error" });

  row.addEventListener("submit", async (e) => {
    e.preventDefault();
    const model = input.value.trim();
    if (!model || model === kb.embed_model) return;
    error.textContent = "";
    submit.disabled = true;
    try {
      await api(`/kb/${encodeURIComponent(kb.name)}/model`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ embed_model: model }),
      });
      await loadKb();
    } catch (err) {
      error.textContent = err.message;
      submit.disabled = false;
    }
  });
  row.append(label, input, submit, error);
  return row;
}

function chunkingControl(kb) {
  const row = document.createElement("form");
  row.className = "kb-model";
  const label = Object.assign(document.createElement("span"), { className: "kb-model-label", textContent: "Chunk size / overlap" });
  const size = Object.assign(document.createElement("input"), { type: "number", min: "1", value: kb.chunk_size, style: "max-width: 90px" });
  size.setAttribute("aria-label", `Chunk size for ${kb.name}`);
  const overlap = Object.assign(document.createElement("input"), { type: "number", min: "0", value: kb.chunk_overlap, style: "max-width: 90px" });
  overlap.setAttribute("aria-label", `Chunk overlap for ${kb.name}`);
  const submit = Object.assign(document.createElement("button"), { type: "submit", className: "settings-btn-secondary", textContent: "Change" });
  const error = Object.assign(document.createElement("div"), { className: "perm-error" });

  row.addEventListener("submit", async (e) => {
    e.preventDefault();
    const chunkSize = parseInt(size.value, 10);
    const chunkOverlap = parseInt(overlap.value, 10);
    if (chunkSize === kb.chunk_size && chunkOverlap === kb.chunk_overlap) return;
    error.textContent = "";
    submit.disabled = true;
    try {
      await api(`/kb/${encodeURIComponent(kb.name)}/chunking`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chunk_size: chunkSize, chunk_overlap: chunkOverlap }),
      });
      await loadKb();
    } catch (err) {
      error.textContent = err.message;
      submit.disabled = false;
    }
  });
  row.append(label, size, overlap, submit, error);
  return row;
}

function docRow(kb, doc, onRemoved) {
  // one row of the ingestion pipeline: a document, expandable into its chunks and each chunk's embedding size
  const row = document.createElement("details");
  row.className = "kb-doc-row";

  const summary = document.createElement("summary");
  const remove = Object.assign(document.createElement("button"), { type: "button", className: "icon-btn", title: "Remove document" });
  remove.setAttribute("aria-label", `Remove ${doc.source}`);
  remove.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  remove.addEventListener("click", async (e) => {
    e.preventDefault(); // this button sits inside <summary>; don't also toggle the details
    remove.disabled = true;
    try {
      await api(`/kb/${encodeURIComponent(kb.name)}/documents/${encodeURIComponent(doc.source)}`, { method: "DELETE" });
      await onRemoved();
      await loadKb();
    } catch (err) {
      alert(err.message);
      remove.disabled = false;
    }
  });
  summary.append(
    Object.assign(document.createElement("span"), { className: "kb-doc-name", textContent: doc.source }),
    Object.assign(document.createElement("span"), { className: "kb-doc-chunks", textContent: `${doc.chunks} chunk(s)` }),
    remove,
  );

  const chunkList = document.createElement("div");
  chunkList.className = "kb-chunk-list";
  let loaded = false;
  row.addEventListener("toggle", async () => {
    if (!row.open || loaded) return;
    loaded = true;
    chunkList.replaceChildren(Object.assign(document.createElement("div"), { className: "side-empty", textContent: "Loading…" }));
    try {
      const chunks = await api(`/kb/${encodeURIComponent(kb.name)}/chunks?source=${encodeURIComponent(doc.source)}`);
      chunkList.replaceChildren();
      for (const chunk of chunks) {
        const line = document.createElement("div");
        line.className = "kb-chunk-row";
        line.append(
          Object.assign(document.createElement("span"), { className: "kb-chunk-index", textContent: `#${chunk.chunk_index + 1}` }),
          Object.assign(document.createElement("span"), { className: "kb-chunk-text", textContent: chunk.text }),
          Object.assign(document.createElement("span"), { className: "kb-chunk-dim", textContent: `→ ${chunk.dimensions}-dim vector` }),
        );
        chunkList.append(line);
      }
    } catch (err) {
      chunkList.replaceChildren(Object.assign(document.createElement("div"), { className: "perm-error", textContent: err.message }));
    }
  });

  row.append(summary, chunkList);
  return row;
}

function documentsList(kb, suggestedModels) {
  const box = document.createElement("div");
  box.className = "kb-docs";
  if (kb.name !== "default") box.append(modelControl(kb, suggestedModels), chunkingControl(kb));
  const hint = Object.assign(document.createElement("p"), {
    className: "kb-model-hint",
    textContent: kb.name === "default"
      ? `Embeds with ${kb.embed_model}, chunked at ${kb.chunk_size}/${kb.chunk_overlap} `
        + "(set rag_embed_model / rag_chunk_size / rag_chunk_overlap in config.json to change them)."
      : "Changing either setting does not re-embed or re-chunk existing documents by itself: run /ingest after switching to it.",
  });
  box.append(hint);
  const list = document.createElement("div");
  list.className = "kb-doc-list";
  list.append(Object.assign(document.createElement("div"), { className: "side-empty", textContent: "Loading…" }));
  box.append(list);

  async function refresh() {
    let docs;
    try {
      docs = await api(`/kb/${encodeURIComponent(kb.name)}/documents`);
    } catch (err) {
      list.replaceChildren(Object.assign(document.createElement("div"), { className: "perm-error", textContent: err.message }));
      return;
    }
    list.replaceChildren();
    if (!docs.length) {
      list.append(Object.assign(document.createElement("div"), { className: "side-empty", textContent: "No documents yet." }));
    }
    for (const doc of docs) {
      list.append(docRow(kb, doc, refresh));
    }
  }

  const upload = document.createElement("form");
  upload.className = "kb-upload";
  const file = Object.assign(document.createElement("input"), { type: "file", accept: ".txt,.md,.pdf" });
  file.setAttribute("aria-label", `Add a document to ${kb.name}`);
  const submit = Object.assign(document.createElement("button"), { type: "submit", className: "settings-btn-primary", textContent: "Add document" });
  const error = Object.assign(document.createElement("div"), { className: "perm-error" });
  upload.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!file.files.length) return;
    error.textContent = "";
    submit.disabled = true;
    submit.textContent = "Adding…";
    try {
      const body = new FormData();
      body.append("file", file.files[0]);
      await api(`/kb/${encodeURIComponent(kb.name)}/documents`, { method: "POST", body });
      file.value = "";
      await refresh();
      await loadKb();
    } catch (err) {
      error.textContent = err.message;
    } finally {
      submit.disabled = false;
      submit.textContent = "Add document";
    }
  });
  upload.append(file, submit);

  box.append(upload, error);
  refresh();
  return box;
}

function tryResultRow(result) {
  const row = document.createElement("div");
  row.className = "kb-try-row" + (result.passed ? " pass" : "");
  row.append(
    Object.assign(document.createElement("span"), { className: "kb-try-rank", textContent: `#${result.rank}` }),
    Object.assign(document.createElement("span"), { className: "kb-try-score", textContent: result.score.toFixed(4) }),
    Object.assign(document.createElement("span"), { className: "kb-try-mark", textContent: result.passed ? "passed" : "below threshold" }),
    Object.assign(document.createElement("span"), { className: "kb-try-source", textContent: `${result.source}, chunk ${result.chunk_index + 1}` }),
    Object.assign(document.createElement("span"), { className: "kb-try-text", textContent: result.text }),
  );
  return row;
}

function tryBox(kb) {
  const box = document.createElement("div");
  box.className = "kb-try";
  box.append(Object.assign(document.createElement("div"), {
    className: "kb-model-label",
    textContent: "Try a question: see every chunk ranked, which passed the threshold, and what gets injected",
  }));

  const form = document.createElement("form");
  form.className = "kb-try-form";
  const question = Object.assign(document.createElement("input"), { placeholder: "Ask a question against this knowledge base…" });
  question.setAttribute("aria-label", `Test question for ${kb.name}`);
  const submit = Object.assign(document.createElement("button"), { type: "submit", className: "settings-btn-secondary", textContent: "Test" });
  form.append(question, submit);

  const error = Object.assign(document.createElement("div"), { className: "perm-error" });
  const results = document.createElement("div");
  results.className = "kb-try-results";
  results.hidden = true;
  const resultsList = document.createElement("div");
  resultsList.className = "kb-try-list";
  const injectedLabel = Object.assign(document.createElement("div"), { className: "kb-model-label", textContent: "What gets injected into the prompt" });
  const injected = document.createElement("pre");
  injected.className = "kb-injected";
  results.append(resultsList, injectedLabel, injected);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = question.value.trim();
    if (!text) return;
    error.textContent = "";
    submit.disabled = true;
    submit.textContent = "Testing…";
    try {
      const data = await api(`/kb/${encodeURIComponent(kb.name)}/try`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text }),
      });
      resultsList.replaceChildren(
        Object.assign(document.createElement("div"), {
          className: "kb-try-head",
          textContent: `top_k=${data.top_k}, min_score=${data.min_score}`,
        }),
        ...data.results.map(tryResultRow),
      );
      injected.textContent = data.injected_prompt;
      results.hidden = false;
    } catch (err) {
      error.textContent = err.message;
    } finally {
      submit.disabled = false;
      submit.textContent = "Test";
    }
  });

  box.append(form, error, results);
  return box;
}

function kbRow(kb, suggestedModels) {
  const block = document.createElement("details");
  block.className = "kb-item";

  const summary = document.createElement("summary");
  summary.append(
    Object.assign(document.createElement("span"), { className: "kb-dot " + (kb.active ? "ok" : "") }),
    Object.assign(document.createElement("span"), { className: "kb-name", textContent: kb.name }),
    Object.assign(document.createElement("span"), {
      className: "kb-state",
      textContent: `${kb.documents} document(s), ${kb.chunks} chunk(s) · ${kb.embed_model} · ${kb.chunk_size}/${kb.chunk_overlap}`,
    }),
  );

  const actions = document.createElement("div");
  actions.className = "srv-actions";

  const use = Object.assign(document.createElement("button"), {
    type: "button",
    textContent: kb.active ? "Used by this chat" : "Use for this chat",
    disabled: kb.active,
  });
  use.addEventListener("click", async (e) => {
    e.preventDefault(); // this button sits inside <summary>; don't also toggle the details
    use.disabled = true;
    try {
      await api(`/kb/${encodeURIComponent(kb.name)}/use`, { method: "POST" });
      await loadKb();
    } catch (err) {
      alert(err.message);
      use.disabled = kb.active;
    }
  });
  actions.append(use);

  if (kb.name !== "default") {
    const remove = Object.assign(document.createElement("button"), { type: "button", textContent: "Delete" });
    remove.addEventListener("click", async (e) => {
      e.preventDefault();
      if (!confirm(`Delete the knowledge base "${kb.name}" and its documents? This cannot be undone.`)) return;
      remove.disabled = true;
      try {
        await api(`/kb/${encodeURIComponent(kb.name)}`, { method: "DELETE" });
        await loadKb();
      } catch (err) {
        alert(err.message);
        remove.disabled = false;
      }
    });
    actions.append(remove);
  }

  summary.append(actions);

  block.append(summary, documentsList(kb, suggestedModels), tryBox(kb));
  return block;
}

function createForm(suggestedModels) {
  const form = document.createElement("form");
  form.className = "kb-create";
  const name = Object.assign(document.createElement("input"), { type: "text", placeholder: "New knowledge base name", spellcheck: false });
  name.setAttribute("aria-label", "New knowledge base name");
  const model = Object.assign(document.createElement("input"), {
    placeholder: `Embedding model (default: ${suggestedModels[0] || "nomic-embed-text"})`,
    spellcheck: false,
  });
  model.setAttribute("list", "kb-model-suggestions");
  model.setAttribute("aria-label", "Embedding model for the new knowledge base");
  const size = Object.assign(document.createElement("input"), { type: "number", min: "1", placeholder: "Chunk size", style: "max-width: 100px" });
  size.setAttribute("aria-label", "Chunk size for the new knowledge base");
  const overlap = Object.assign(document.createElement("input"), { type: "number", min: "0", placeholder: "Overlap", style: "max-width: 90px" });
  overlap.setAttribute("aria-label", "Chunk overlap for the new knowledge base");
  const submit = Object.assign(document.createElement("button"), { type: "submit", className: "settings-btn-primary", textContent: "Create" });
  const error = Object.assign(document.createElement("div"), { className: "perm-error" });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!name.value.trim()) return;
    error.textContent = "";
    submit.disabled = true;
    try {
      await api("/kb", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.value.trim(),
          embed_model: model.value.trim() || null,
          chunk_size: size.value ? parseInt(size.value, 10) : null,
          chunk_overlap: overlap.value ? parseInt(overlap.value, 10) : null,
        }),
      });
      name.value = "";
      model.value = "";
      size.value = "";
      overlap.value = "";
      await loadKb();
    } catch (err) {
      error.textContent = err.message;
    } finally {
      submit.disabled = false;
    }
  });
  form.append(name, model, size, overlap, submit, error);
  return form;
}

function renderKb({ collections, suggested_models: suggestedModels }) {
  kbBody.replaceChildren();
  const intro = document.createElement("p");
  intro.className = "perm-intro";
  intro.textContent = "Each knowledge base has its own documents, search index and embedding model. A chat retrieves from whichever one is in use for it.";
  const datalist = Object.assign(document.createElement("datalist"), { id: "kb-model-suggestions" });
  datalist.append(...suggestedModels.map((m) => Object.assign(document.createElement("option"), { value: m })));
  kbBody.append(intro, datalist);

  const list = document.createElement("div");
  list.className = "kb-list";
  list.append(...collections.map((kb) => kbRow(kb, suggestedModels)));
  kbBody.append(list, createForm(suggestedModels));
}

async function loadKb() {
  renderKb(await api("/kb"));
}

async function openKb() {
  closeSidebar();
  try {
    await loadKb();
  } catch (err) {
    alert(err.message);
    return;
  }
  kbDialog.showModal();
}

export function initKb() {
  document.getElementById("open-kb").addEventListener("click", openKb);
  document.getElementById("kb-close").addEventListener("click", () => kbDialog.close());
}
