const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "mneme.writeToken";
let debounceTimer;

function loadToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}

function rememberToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* private storage may be unavailable */ }
}

function formatDate(value) {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf())
    ? value
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(parsed);
}

function metadataLine(doc) {
  return [
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
    meta.textContent = metadataLine(doc) || "Captured research";
    copy.append(title, meta);
    const project = document.createElement("span");
    project.className = "project-label";
    project.textContent = doc.project_id;
    button.append(copy, project);
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
  if (q) params.set("q", q);
  if (project) params.set("project_id", project);
  if (tag) params.set("tag", tag);
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
  const doc = await fetchJson(`/api/documents/${encodeURIComponent(id)}`);
  $("reader-project").textContent = doc.project_id;
  $("reader-title").textContent = doc.title;
  $("reader-meta").textContent = metadataLine(doc);
  $("reader-body").textContent = doc.body;

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
  $("document-list").parentElement.hidden = true;
  $("reader").hidden = false;
  $("reader").focus();
}

$("close-reader").addEventListener("click", () => {
  $("reader").hidden = true;
  $("document-list").parentElement.hidden = false;
});

for (const id of ["search", "project-filter", "tag-filter"]) {
  $(id).addEventListener("input", () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(refresh, 180);
  });
}

const dialog = $("ingest-dialog");
$("new-document").addEventListener("click", () => dialog.showModal());
$("close-dialog").addEventListener("click", () => dialog.close());
$("write-token").value = loadToken();

$("ingest-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = new FormData(form);
  const token = String(values.get("token") || "").trim();
  const metadata = {
    title: String(values.get("title") || "").trim(),
    project_id: String(values.get("project_id") || "").trim(),
    source_url: String(values.get("source_url") || "").trim() || null,
    author: String(values.get("author") || "").trim() || null,
    publisher: String(values.get("publisher") || "").trim() || null,
    tags: String(values.get("tags") || "").split(",").map((tag) => tag.trim()).filter(Boolean),
  };
  const payload = new FormData();
  payload.set("metadata", JSON.stringify(metadata));
  payload.set("body", String(values.get("body") || ""));
  for (const file of values.getAll("attachments")) {
    if (file instanceof File && file.size) payload.append("attachments", file);
  }

  const status = $("ingest-status");
  status.textContent = "Storing…";
  try {
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
  } catch (error) {
    if (error.status === 403) {
      rememberToken("");
      $("write-token").value = "";
    }
    status.textContent = `Failed: ${error.message}`;
  }
});

checkHealth();
refresh();
