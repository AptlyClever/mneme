# AGENTS.md — Mneme

Mneme is the Control Alt **durable cross-project research library**: markdown
documents with linked attachments, open read API for LAN agents, token-gated
write API for ingestion. It is a standalone leaf, not part of Axiom or Vellum.

## Classification

- **Standalone Control Alt leaf** (like Bandit, Vellum). New product → **plain
  name** `mneme`; the retired `ctrl-alt-*` prefix is not used.
- **First consumer:** Bandit — but Mneme has **no project ownership**. It stores
  research for any product and any LAN agent on equal terms.
- **Registry identity:** Axiom `config/apps.registry.yaml` `id: mneme`; leaf app
  uses the same value for `CTRL_ALT_APP_ID`.
- **Data vault:** `/mnt/data/vault/mneme` on the press (private; never commit
  research bytes or secrets).

## Read first

1. `README.md` — identity, paths, run.
2. `PRODUCT.md` — product intent, boundaries, capture model.
3. `ARCHITECTURE.md` — runtime shape, vault layout, studio/press model.
4. `docs/api.md` — open read + token-gated write contract.

## Boundaries (HARD — do not cross)

1. **Not Axiom control-center functionality.** No registry, theme/branding
   authority, cross-app health, per-app settings, or Repo Ops logic in this
   repo. Consume Axiom effective settings; never act as the hub or a second SoT
   for hub-managed fields.
2. **Not the Vellum production / visual asset pipeline.** No conversion,
   lookdev, render, sprite-sheet, or game-ready work. Mneme stores research
   prose + reference material and emits no production assets.
3. **No crawler or extractor (v1).** Do not add a fetch loop, scraper, or
   headless browser. **Capture agents transform sources and upload** via the
   write API; Mneme only stores, indexes, and serves.
4. **No project ownership.** Mneme is a shared library, not the system of record
   for Bandit or any other product. Do not fold another product's authoritative
   state into it.

## Access rules

- **Read is open** to LAN agents — no token. Any agent may list, search, get a
  document, and fetch attachment bytes.
- **Write is token-gated** — `Authorization: Bearer $MNEME_WRITE_TOKEN`.
  Unauthorized writes return `403 mneme_read_only`.
- The write token lives at `/mnt/temp/config/mneme/.env` on the press
  (gitignored). Read-only consumers (Bandit included) **must never** receive it.
  Never bake it into a repo, doc, handoff, or Cue.

## Environment roles (studio / press)

Same model as Axiom and Vellum — do not collapse these:

| Role | Machine | Path | Job |
| --- | --- | --- | --- |
| **Studio** | Any dev machine (Borealis, …) | `E:\Dev\mneme` | Read, code, test, commit, **push** |
| **Press** | `dev-ubuntu` (`192.168.68.93`) | `/mnt/temp/config/mneme` | Repo Ops deploy checkout; Docker runtime; API `:8790`; vault mount |

- `/mnt/temp/config/mneme` and `/mnt/data/vault/mneme` exist **only on the
  press**; not reachable from studio. **Vault I/O is HTTP-only** off the press.
- Never run the Mneme compose stack from a studio checkout as "the runtime."

## Working rules

- Prefer the vault + catalog index over dumping research into app repos.
- Never store the write token, or any secret, in git or Axiom registry files.
- Keep attachments beneath their owning document directory; links never float
  free of a document.
- Do not add crawling/extraction to v1. If a source needs fetching, that is a
  capture-agent (external) responsibility.
- Do not mutate Axiom / Praxis / Vellum / other apps' runtime infra unless the
  operator explicitly asks.
- Port `8790`, app id `mneme`, vault `/mnt/data/vault/mneme` are fixed
  identifiers — keep them consistent across compose, docs, and registry.

## Ship path (Repo Ops)

Deploy always goes through **Axiom Repo Ops** (`#/axiom/repo-ops`) after a
single operator confirmation — see `.cursor/rules/repo-ops-ship.mdc`:

1. **Commit** (agent or Repo Ops `git.commit`).
2. **Push** to GitHub (`git push` from studio, or Repo Ops `git.push`).
3. **Deploy** via Repo Ops (`deploy.auto` / `deploy.build`) against the `mneme`
   project once registered.

Do not stand up a parallel manual deploy path from an agent or dev box. This
doc set does not commit, push, or deploy on its own.
