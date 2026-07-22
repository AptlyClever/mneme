# Architecture — Mneme

## Identity

**Mneme is a standalone Control Alt leaf app** — a durable cross-project
research library. It stores markdown documents with linked attachments, serves
them over an **open read API** for LAN agents, and accepts writes only through a
**token-gated ingestion API**. It is a sibling to Bandit and Vellum, not part of
Axiom and not part of the Vellum asset pipeline.

## Product split

| Surface | Role | Direction |
| --- | --- | --- |
| **Library API** | Open read (list/search/get document + attachment bytes) | Primary — the durable, agent-facing contract |
| **Ingestion API** | Token-gated write (create/update/delete document, upload attachment) | Fed by external capture agents; never a crawler |
| **Vault store** | Markdown + attachments + index under `/mnt/data/vault/mneme` | Durable source of truth for library bytes (research and plans) |
| **Operator UI** | Browse/search library, manual upload | Thin; consumes Axiom effective settings for chrome |

## What lives where

- **This repo (`E:\Dev\mneme`)** — application code, docs, compose, Dockerfile.
  Contains **no** research bytes and **no** secrets.
- **Vault (`/mnt/data/vault/mneme`, press only)** — the durable library: document
  markdown, attachment files, and the catalog index. Never committed to git.
- **`.env` (press only)** — `MNEME_WRITE_TOKEN` and any other secrets. Gitignored.

## Stack

- **Backend:** FastAPI (Python), file-backed stores. Each document directory
  contains `body.md`, `manifest.json`, and its attachment bytes. The filesystem
  is the source of truth; search derives from those durable files.
- **Deploy:** Docker Compose on the homelab press; operator ship path is Axiom
  Repo Ops (`#/axiom/repo-ops`).
- **Port:** `8790` (host and container).

## Vault layout

The vault is the durable store; the repo only mounts it at runtime.

```
/mnt/data/vault/mneme/
  <document-id>/
    body.md                 # markdown body
    manifest.json           # metadata and attachment checksums
    attachments/
      <filename>            # linked files (images, PDFs, captures, data)
```

- Each document owns a directory; its attachments live beneath it so links never
  float free.
- The document directories are authoritative and independently portable.
- Read endpoints jail all file access under the vault root — no path escapes.

## Access model

| Surface | Auth | Failure |
| --- | --- | --- |
| Read (list, search, get, attachment bytes) | none (open to LAN) | `404 mneme_not_found` for missing ids |
| Write (create/update/delete, upload) | `Authorization: Bearer $MNEME_WRITE_TOKEN` | `403 mneme_read_only` when unset/wrong |

The write token is a shared secret whose canonical home is
`/mnt/temp/config/mneme/.env` on the press. It is gitignored, survives Repo Ops
deploys (`git clean -fd` preserves gitignored files), and is read automatically
by Docker Compose for the `${MNEME_WRITE_TOKEN:-}` substitution. Read-only
consumers (Bandit and other LAN agents) must never receive it.

## Studio / press runtime model (binding)

Same model as Axiom (`ctrl-alt-axiom/docs/handbook/how-we-work.md`) and Vellum.
Do not collapse these roles into one path:

| Role | Machine | Path | Job |
| --- | --- | --- | --- |
| **Studio** | Any dev machine (Borealis, …) | GitHub clone, e.g. `E:\Dev\mneme` | Read, design, implement, run local tests, commit, **push** |
| **Press** | `dev-ubuntu` (`192.168.68.93`) | `/mnt/temp/config/mneme` | Repo Ops deploy checkout; Docker runtime; API `:8790`; vault mount |

- `/mnt/temp/config/mneme` and `/mnt/data/vault/mneme` exist **only on the
  press**. From studio they are not reachable filesystems — no WSL/UNC/SSH file
  access to the vault.
- **Vault I/O is HTTP-only** off the press: read via the open API, write via the
  token-gated API. Never hand-edit catalog files from another machine.
- Never run the Mneme compose stack from a studio checkout as "the runtime."
  The studio checkout is for implementation feedback only.

## Repo Ops ship path

Deploy always goes through **Axiom Repo Ops** — never a parallel manual deploy
from an agent or dev box:

1. **Commit** current changes (agent or Repo Ops `git.commit`).
2. **Push** to GitHub (`git push` from studio, or Repo Ops `git.push`).
3. **Deploy** via Repo Ops (`#/axiom/repo-ops`) — `deploy.auto` /
   `deploy.build` against the `mneme` project once registered.

Confirm with the operator once before commit/push/deploy (see
`.cursor/rules/repo-ops-ship.mdc`). This repo's doc set does **not** commit,
push, or deploy on its own.

## Boundaries (architecture-level)

- **Not Axiom:** no registry, theme SoT, cross-app health, or Repo Ops logic
  here. Mneme reads Axiom effective settings for chrome only.
- **Not Vellum:** no conversion, render, or game-ready catalog. No production
  asset outputs.
- **No crawler/extractor (v1):** no fetch loop, no headless browser. Capture
  agents transform sources and upload; Mneme stores and serves.
- **No project ownership:** Mneme is a shared library. It is not the system of
  record for Bandit or any other product's operational data.

## Axiom integration

- Registry identity for fleet discovery lives in Axiom
  `config/apps.registry.yaml` (`id: mneme`); leaf app uses the same value for
  `CTRL_ALT_APP_ID`.
- Chrome/branding come from Axiom effective settings; Mneme embeds no
  operator-specific names, logos, or theme hex as overriding defaults.
- Mneme never mutates hub-managed fields or other apps' runtime state.
