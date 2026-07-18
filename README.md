# Mneme

**Mneme** is the Control Alt **durable cross-project research library**. It
stores long-lived research as **markdown documents with linked attachments**,
served over an **open read API** for LAN agents and gated behind a write token
for ingestion. It is a shared reference shelf, not a project owner: Bandit is
its first consumer, but Mneme belongs to no single product.

| Fact | Value |
| --- | --- |
| App / registry id | `mneme` |
| Studio (dev) root | `E:\Dev\mneme` (this repo) |
| Press (runtime) root | `/mnt/temp/config/mneme` on `dev-ubuntu` |
| Private vault (data) | `/mnt/data/vault/mneme` |
| Operator UI / API | http://192.168.68.93:8790/ |
| Health | http://192.168.68.93:8790/api/health |
| Read API | open to LAN agents (no token) |
| Write API | `Authorization: Bearer $MNEME_WRITE_TOKEN` |
| Secrets | `/mnt/temp/config/mneme/.env` (gitignored) |
| Not | Axiom control-center · Vellum asset pipeline · a crawler/extractor |

## What Mneme is

- A **durable library** of research notes that outlive any one project or chat.
- **Documents** are markdown; each may link **attachments** (images, PDFs,
  captured pages, data files) stored beside it in the vault.
- **Read is open** so any LAN agent can search and cite research without a
  secret. **Write is token-gated** so only trusted capture tools and operators
  add or change documents.

## What Mneme is not

- **Not Axiom control-center functionality.** Mneme does not manage the
  registry, themes, branding, health of other apps, or Repo Ops. It consumes
  Axiom effective settings; it never competes as the hub.
- **Not the Vellum production / visual asset pipeline.** Vellum converts and
  catalogs game-ready art. Mneme stores research prose and reference material
  and produces no game-ready or render artifacts.
- **Not a crawler or extractor (v1).** Mneme does not fetch, scrape, or parse
  the open web itself. External **capture agents** transform sources into
  markdown + attachments and **upload** them through the write API.
- **Not doctrine, not agent memory.** Canonical authored docs (how-we-work,
  runbooks, standards) live in git and render in Axiom (Handbook / Ops
  Reference). Mutable agent session memory is a different lifecycle and a
  separate future decision. Mneme holds **captured research with provenance**
  — documents with a `source_url` and `captured_at`. See `AGENTS.md`
  boundary 5.

## Run

```bash
# runs on the press (dev-ubuntu); studio is for editing/testing only
docker compose up -d --build
# health
curl -sS http://127.0.0.1:8790/api/health
```

## Start here

| Doc | What it is |
| --- | --- |
| **[PRODUCT.md](./PRODUCT.md)** | Product intent, boundaries, first consumer (Bandit) |
| **[ARCHITECTURE.md](./ARCHITECTURE.md)** | Runtime shape, vault layout, studio/press model |
| **[docs/api.md](./docs/api.md)** | Open read API + token-gated write API contract |
| **[AGENTS.md](./AGENTS.md)** | Agent rules, boundaries, ship path |

## Boundaries (short form)

- Research bytes and the write token stay under the press / vault, never in git.
- Read endpoints require no secret; write endpoints require `MNEME_WRITE_TOKEN`.
- Mneme owns no project's canonical data — it is a library, not a system of
  record for another product.
