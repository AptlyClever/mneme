const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "mneme.writeToken";
let debounceTimer;
let currentDocId = null;
let dialogMode = "create";

function loadToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}

function rememberToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* private storage may be unavailable */ }
}

function hasToken() {
  return !!loadToken();
}

function formatDate(value) {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf())
    ? value
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(parsed);
}

function metadataLine(doc) {
  const functionLabel = doc.function && doc.function !== "research"
    ? doc.function.toUpperCase()
    : null;
  const statusLabel = doc.status && doc.status !== "active"
    ? doc.status
    : null;
  return [
    functionLabel,
    statusLabel,
    doc.author,
    doc.publisher,
    formatDate(doc.published_at || doc.captured_at),
    ...(doc.tags || []).map((tag) => `#${tag}`),
  ].filter(Boolean).join(" · ");
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.detail || response.statusText || "Request failed");
    error.status = response.status;
    throw error;
  }
  return body;
}

function renderList(items) {
  const list = $("document-list");
  list.replaceChildren();
  $("empty").hidden = items.length > 0;
  for (const doc of items) {
    const item = document.createElement("li");
    item.className = "document-item";
    const button = document.createElement("button");
    button.type = "button";
    const copy = document.createElement("span");
    const title = document.createElement("h2");
    title.textContent = doc.title;
    const meta = document.createElement("p");
    meta.textContent = metadataLine(doc) || (doc.function === "plan" ? "Authored plan" : "Captured knowledge");
    copy.append(title, meta);
    const badges = document.createElement("span");
    badges.className = "item-badges";
    if (doc.function && doc.function !== "research") {
      const fn = document.createElement("span");
      fn.className = "function-label";
      fn.textContent = doc.function;
      badges.append(fn);
    }
    const project = document.createElement("span");
    project.className = "project-label";
    project.textContent = doc.project_id;
    badges.append(project);
    button.append(copy, badges);
    button.addEventListener("click", () => openDocument(doc.id));
    item.append(button);
    list.append(item);
  }
}

async function refresh() {
  const params = new URLSearchParams();
  const q = $("search").value.trim();
  const project = $("project-filter").value.trim();
  const tag = $("tag-filter").value.trim();
  const fn = $("function-filter").value.trim();
  const status = $("status-filter")?.value.trim();
  if (q) params.set("q", q);
  if (project) params.set("project_id", project);
  if (tag) params.set("tag", tag);
  if (fn) params.set("function", fn);
  if (status) params.set("status", status);
  try {
    const data = await fetchJson(`/api/documents?${params}`);
    renderList(data.items);
    $("count").textContent = `${data.total} document${data.total === 1 ? "" : "s"}`;
  } catch (error) {
    $("count").textContent = "Library unavailable";
    $("empty").hidden = false;
    $("empty").querySelector("p").textContent = error.message;
  }
}

async function checkHealth() {
  try {
    const health = await fetchJson("/api/health");
    $("health").textContent = health.writes_enabled ? "Vault online · writes enabled" : "Vault online · read only";
    $("health").classList.add("ok");
  } catch {
    $("health").textContent = "Vault unavailable";
  }
}

async function openDocument(id) {
  currentDocId = id;
  const doc = await fetchJson(`/api/documents/${encodeURIComponent(id)}`);
  $("reader-project").textContent = doc.function && doc.function !== "research"
    ? `${doc.project_id} · ${doc.function}`
    : doc.project_id;
  $("reader-title").textContent = doc.title;
  $("reader-meta").textContent = metadataLine(doc);
  $("reader-body").innerHTML = DOMPurify.sanitize(marked.parse(doc.body || ""));

  const list = $("attachments");
  list.replaceChildren();
  for (const attachment of doc.attachments || []) {
    const item = document.createElement("li");
    const link = document.createElement("a");
    link.href = `/api/documents/${encodeURIComponent(id)}/attachments/${encodeURIComponent(attachment.filename)}`;
    link.textContent = `${attachment.filename} (${Math.ceil(attachment.size / 1024)} KB)`;
    link.target = "_blank";
    link.rel = "noopener";
    item.append(link);
    list.append(item);
  }
  $("attachments-section").hidden = !doc.attachments?.length;
  $("reader-actions").hidden = !hasToken();
  $("document-list").parentElement.hidden = true;
  $("reader").hidden = false;
  $("reader").focus();
}

function closeReader() {
  currentDocId = null;
  $("reader").hidden = true;
  $("document-list").parentElement.hidden = false;
}

function updateProvenanceVisibility() {
  const form = $("ingest-form");
  const sourceType = form.source_type.value;
  const isHuman = sourceType === "human_capture";
  $("agent-id-field").hidden = isHuman;
  $("session-id-field").hidden = isHuman;
  const sourceUrlField = form.source_url.closest("label");
  if (sourceUrlField) {
    sourceUrlField.querySelector("span").textContent = isHuman
      ? "Source URL (optional)"
      : "Source URL (required for research)";
  }
}

function resetDialogToCreate() {
  dialogMode = "create";
  $("dialog-title").textContent = "Add a document";
  $("submit-button").textContent = "Store in Mneme";
  $("attachments-field").hidden = false;
  $("ingest-form").reset();
  $("write-token").value = loadToken();
  $("ingest-status").textContent = "";
  updateProvenanceVisibility();
}

function openEditDialog() {
  if (!currentDocId) return;
  fetchJson(`/api/documents/${encodeURIComponent(currentDocId)}`).then((doc) => {
    dialogMode = "edit";
    $("dialog-title").textContent = "Edit document";
    $("submit-button").textContent = "Save changes";
    $("attachments-field").hidden = true;
    const form = $("ingest-form");
    form.title.value = doc.title || "";
    form.project_id.value = doc.project_id || "";
    form.function.value = doc.function || "research";
    form.source_url.value = doc.source_url || "";
    form.author.value = doc.author || "";
    form.publisher.value = doc.publisher || "";
    form.tags.value = (doc.tags || []).join(", ");
    form.body.value = doc.body || "";
    form.status.value = doc.status || "active";
    const provenance = doc.provenance || {};
    form.source_type.value = provenance.source_type || "human_capture";
    form.agent_id.value = provenance.agent_id || "";
    form.session_id.value = provenance.session_id || "";
    $("write-token").value = loadToken();
    $("ingest-status").textContent = "";
    updateProvenanceVisibility();
    $("ingest-dialog").showModal();
  });
}

function deleteDocument() {
  if (!currentDocId) return;
  if (!confirm("Delete this document? This cannot be undone.")) return;
  const token = loadToken();
  const status = $("ingest-status");
  fetchJson(`/api/documents/${encodeURIComponent(currentDocId)}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  }).then(() => {
    closeReader();
    refresh();
  }).catch((error) => {
    if (error.status === 403) {
      rememberToken("");
      $("write-token").value = "";
      $("reader-actions").hidden = true;
    }
    alert(`Delete failed: ${error.message}`);
  });
}

$("close-reader").addEventListener("click", closeReader);
$("edit-document").addEventListener("click", openEditDialog);
$("delete-document").addEventListener("click", deleteDocument);

for (const id of ["search", "project-filter", "tag-filter"]) {
  $(id).addEventListener("input", () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(refresh, 180);
  });
}
$("function-filter").addEventListener("change", refresh);
if ($("status-filter")) {
  $("status-filter").addEventListener("change", refresh);
}

const dialog = $("ingest-dialog");
$("new-document").addEventListener("click", () => {
  resetDialogToCreate();
  dialog.showModal();
});
$("close-dialog").addEventListener("click", () => dialog.close());
$("write-token").value = loadToken();
if ($("ingest-form").source_type) {
  $("ingest-form").source_type.addEventListener("change", updateProvenanceVisibility);
}

$("ingest-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = new FormData(form);
  const token = String(values.get("token") || "").trim();
  const sourceType = String(values.get("source_type") || "human_capture").trim();
  const sourceUrl = String(values.get("source_url") || "").trim() || null;
  const agentId = String(values.get("agent_id") || "").trim() || null;
  const sessionId = String(values.get("session_id") || "").trim() || null;

  const metadata = {
    title: String(values.get("title") || "").trim(),
    project_id: String(values.get("project_id") || "").trim(),
    function: String(values.get("function") || "research").trim() || "research",
    source_url: sourceUrl,
    author: String(values.get("author") || "").trim() || null,
    publisher: String(values.get("publisher") || "").trim() || null,
    tags: String(values.get("tags") || "").split(",").map((tag) => tag.trim()).filter(Boolean),
    status: String(values.get("status") || "active").trim() || "active",
    provenance: {
      source_type: sourceType,
      agent_id: agentId,
      session_id: sessionId,
      source_url: sourceUrl,
    },
  };

  const status = $("ingest-status");
  try {
    if (dialogMode === "edit" && currentDocId) {
      status.textContent = "Saving…";
      await fetchJson(`/api/documents/${encodeURIComponent(currentDocId)}`, {
        method: "PATCH",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ...metadata, body: String(values.get("body") || "") }),
      });
      rememberToken(token);
      status.textContent = "Saved.";
      dialog.close();
      await openDocument(currentDocId);
      await refresh();
    } else {
      status.textContent = "Storing…";
      const payload = new FormData();
      payload.set("metadata", JSON.stringify(metadata));
      payload.set("body", String(values.get("body") || ""));
      for (const file of values.getAll("attachments")) {
        if (file instanceof File && file.size) payload.append("attachments", file);
      }
      await fetchJson("/api/documents", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: payload,
      });
      rememberToken(token);
      form.reset();
      $("write-token").value = token;
      status.textContent = "Stored.";
      dialog.close();
      await refresh();
    }
  } catch (error) {
    if (error.status === 403) {
      rememberToken("");
      $("write-token").value = "";
      $("reader-actions").hidden = true;
    }
    status.textContent = `Failed: ${error.message}`;
  }
});

checkHealth();
refresh();