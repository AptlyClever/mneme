# Product — Mneme

## One line

**Mneme is the durable, cross-project research library for Control Alt:**
markdown documents with linked attachments, readable by any LAN agent and
writable only through a token-gated ingestion API.

## Why it exists

Research produced during product work — competitor notes, UI inspiration,
protocol write-ups, captured references, decision background — is currently
scattered across chats, PRs, and one-off files. It dies when a task closes.

Mneme gives that research a **permanent home** that is:

- **Cross-project** — not owned by Bandit, Vellum, or Axiom; any product can
  read it and any capture tool can contribute to it.
- **Durable** — documents survive deploys, chat resets, and product pivots.
- **Agent-native** — the read surface is a plain HTTP API so LAN agents can
  search and cite research directly, with no secret to leak.

## The object model

| Object | What it is |
| --- | --- |
| **Document** | A markdown body stored with a machine-readable manifest (title, tags, source, project association, document function, timestamps). The primary unit. |
| **Attachment** | A file (image, PDF, captured page, data export) linked to exactly one document and stored beside it in the vault. |
| **Collection / tags** | Lightweight grouping so consumers can scope reads (e.g. `bandit`, `ui`, `protocol`) without Mneme owning any project's schema. |
| **Document function** | A small constrained purpose discriminator on each document (`research` or `plan` in v1). Exact-filterable via the API; not a substitute for tags. |

Documents reference attachments by stable id/URL; attachments never float free
of a document.

### Document function

`function` answers *what kind of durable artifact this is*, not which topic it
covers:

| Value | Meaning | Provenance expectation |
| --- | --- | --- |
| `research` (default) | Externally captured, citable research | Prefer a real `source_url` and `captured_at` |
| `plan` | Internally authored forward-looking plan | May omit `source_url`; still durable and versionable |

Existing manifests that omit `function` are treated as `research` without a
vault rewrite. Do not use tags as a substitute for this field. Additional
functions may be added deliberately later; this is not an open ontology.

`plan` is still Mneme content — not Axiom Handbook doctrine and not mutable
agent memory. See `AGENTS.md` boundary 5 for the broader provenance line.

## First consumer: Bandit (no project ownership)

Bandit is the **first consumer** and shapes the initial read contract, but this
does **not** make Mneme a Bandit component:

- Bandit agents **read** research via the open API and cite it in their work.
- Bandit does **not** own Mneme's storage, schema, or lifecycle, and Mneme
  stores research for any product, not just Bandit.
- Other LAN agents (and future products) read the same library on equal terms.

## Explicit boundaries

Mneme deliberately does **not** do these things:

1. **Not Axiom control-center functionality.** No registry, theme/branding
   authority, cross-app health, per-app settings, or Repo Ops. Mneme consumes
   Axiom effective settings; it never acts as the hub or a second source of
   truth for hub-managed fields.
2. **Not the Vellum production / visual asset pipeline.** No conversion,
   lookdev, render, sprite-sheet, or game-ready catalog work. Mneme holds
   research prose and reference material; it emits no production assets.
3. **Not a crawler or extractor (v1).** Mneme does not fetch, scrape, or parse
   the open web. It has no polling loop and no headless browser of its own.
4. **Not a system of record for another product.** Mneme is a library. If a
   product needs authoritative operational state, that lives in the product,
   not here.

## Capture model (how documents get in)

External **capture agents** own the transform-and-upload step:

1. A capture agent (a Bandit tool, a browser agent, an operator script) gathers
   a source — a page, a doc, a set of images.
2. The agent **transforms** it into a Mneme document: markdown body plus any
   attachments.
3. The agent **uploads** via the write API using `MNEME_WRITE_TOKEN`.

Mneme's job is to **store, index, and serve** — not to go get sources itself.
This keeps the v1 surface small and the crawler/extractor complexity outside
the durable library.

## Access model

| Surface | Auth | Who |
| --- | --- | --- |
| **Read** (list, search, get document, get attachment bytes) | none | Any LAN agent or product |
| **Write** (create/update/delete document, upload/delete attachment) | `Authorization: Bearer $MNEME_WRITE_TOKEN` | Capture agents + operators only |

Read-only consumers (Bandit included) must **never** receive the write token.
Unauthorized writes return HTTP **403** `mneme_read_only`.

## Success criteria (v1)

- A capture agent can upload a markdown document with one or more attachments in
  a single, documented flow.
- Any LAN agent can search documents and fetch a readable attachment with no
  secret.
- Research persists across a Repo Ops deploy and a vault-only restart.
- No boundary above is crossed: no crawling, no asset pipeline, no hub duties.

## Naming

`mneme` is a **new** product, so it uses the plain kebab-case name — no
retired `ctrl-alt-*` prefix, no legacy env names. The registry id, compose
project, container/image role names, vault directory, and `CTRL_ALT_APP_ID`
all use `mneme`.
