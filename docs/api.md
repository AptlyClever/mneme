# Mneme API

Mneme stores markdown documents with linked attachments. It is the durable
**Control Alt Knowledge** store: research, plans, synthesis, decisions,
reference, insights, and preferences. Reads are open to LAN consumers. Writes
require `Authorization: Bearer <MNEME_WRITE_TOKEN>` and otherwise return HTTP
403:

```json
{"detail":"mneme_read_only"}
```

Base URL on the press: `http://192.168.68.93:8790/api`

## Document shape

```json
{
  "id": "0db12d50fc9e4a98922162623be77220",
  "project_id": "bandit",
  "function": "research",
  "title": "Slot game development process and market trends",
  "author": "Author name",
  "publisher": "1spin4win",
  "published_at": "2025-01-10T00:00:00Z",
  "source_url": "https://example.com/article",
  "captured_at": "2026-07-18T19:00:00Z",
  "created_at": "2026-07-18T19:00:00Z",
  "updated_at": "2026-07-18T19:00:00Z",
  "tags": ["slots", "market-research"],
  "body": "# Article title\n\nFull markdown body…",
  "attachments": [
    {
      "filename": "market-chart.png",
      "content_type": "image/png",
      "size": 48210,
      "sha256": "…"
    }
  ],
  "provenance": {
    "source_type": "web_page",
    "agent_id": "bandit-capture-agent",
    "session_id": "cfd-inspiration-20260718-001",
    "source_url": "https://example.com/article",
    "confidence": 0.92
  },
  "context_ids": [
    {"type": "cfd", "id": "cfd-inspiration-20260718-001"},
    {"type": "project", "id": "bandit"}
  ],
  "relationships": {
    "parent_id": "aabbcc…",
    "supersedes": ["ddeeff…"],
    "related_ids": ["001122…"]
  },
  "status": "active",
  "confidence": 0.92,
  "reviewed_at": "2026-07-19T12:00:00Z",
  "reviewed_by": "Travis"
}
```

### Document function

`function` is a constrained purpose discriminator for Control Alt Knowledge:

| Value | Use | `source_url` expectation |
| --- | --- | --- |
| `research` | Externally captured knowledge | Required for non-human sources; optional for human capture |
| `plan` | Forward-looking authored plan | Optional |
| `synthesis` | Agent-authored summary from sources/sessions | Optional |
| `decision` | Director/operator decision record | Optional |
| `reference` | Reference material, boards, specs | Optional |
| `insight` | Director's personal insight or evaluation | Optional |
| `preference` | Director's stated preference or working style | Optional |

- `function` is required on create.
- Unknown values are rejected.
- Existing vault documents that omit `function` are treated as `research` without
  rewriting disk.
- Tags remain free-form topic labels and are **not** a substitute for `function`.

### Provenance

The `provenance` block records where a document came from:

| Field | Meaning |
| --- | --- |
| `source_type` | `human_capture`, `agent_synthesis`, `web_page`, `github_issue`, `external_document`, or `unknown` |
| `agent_id` | The capture agent that produced the document |
| `session_id` | The originating session / CFD / Cue identifier |
| `source_url` | Canonical external URL (also mirrored at top-level for backward compatibility) |
| `confidence` | `0.0`–`1.0` (optional) |

**Validation rule:** A `research` document whose `source_type` is **not**
`human_capture` must provide a `source_url`. This captures the contract that
machine/agents must supply provenance, while human operators are more flexible.

### Context IDs and relationships

- `context_ids`: links to Control Alt contexts such as CFDs, Cues, or projects.
  - `type`: `cfd`, `cue`, or `project`
  - `id`: the identifier in that context
- `relationships`: cross-document links
  - `parent_id`: document this extends or answers to
  - `supersedes`: older documents this one replaces
  - `related_ids`: other related documents

### Lifecycle

- `status`: `active` (default), `draft`, `superseded`, or `archived`
- `confidence`: `0.0`–`1.0` (optional)
- `reviewed_at`, `reviewed_by`: review tracking

## Open reads

- `GET /api/health`
- `GET /api/documents?q=&project_id=&tag=&function=&context_type=&context_id=&status=&limit=50&offset=0`
- `GET /api/documents/{document_id}` returns metadata plus the markdown `body`
- `GET /api/documents/{document_id}/context` returns the document's context IDs
- `GET /api/documents/{document_id}/related` returns related documents
- `GET /api/documents/{document_id}/attachments/{filename}` returns bytes with
  the stored MIME type and checksum ETag

Search is a multi-term AND query scored across title, tags, and body. `project_id`,
`tag`, `function`, `context_type`, `context_id`, and `status` add exact filters.
Limits are 1–500.

## Token-gated writes

### Create a document and attachments in one request

`POST /api/documents` is multipart:

- `metadata`: JSON string containing `project_id`, `title`, `function`, and optional
  `author`, `publisher`, `published_at`, `source_url`, `captured_at`, `tags`,
  `provenance`, `context_ids`, `relationships`, `status`, `confidence`,
  `reviewed_at`, `reviewed_by`
- `body`: markdown string
- `attachments`: zero or more files

```bash
curl -sS -X POST "http://192.168.68.93:8790/api/documents" \
  -H "Authorization: Bearer $MNEME_WRITE_TOKEN" \
  -F 'metadata={"project_id":"bandit","function":"research","provenance":{"source_type":"web_page","agent_id":"bandit-capture"},"title":"Slot development trends","publisher":"1spin4win","source_url":"https://example.com/article","tags":["slots","market-research"]}' \
  -F 'body=<./article.md' \
  -F 'attachments=@./market-chart.png'
```

Authored plan example (no external source):

```bash
curl -sS -X POST "http://192.168.68.93:8790/api/documents" \
  -H "Authorization: Bearer $MNEME_WRITE_TOKEN" \
  -F 'metadata={"project_id":"axiom","function":"plan","provenance":{"source_type":"human_capture"},"title":"Example plan","author":"Control Alt","publisher":"Control Alt","tags":["integration-plan"]}' \
  -F 'body=<./plan.md'
```

### Patch a document

`PATCH /api/documents/{document_id}` accepts JSON with any document metadata
field (including `function` and `provenance`) and/or `body`.

### Add attachments

`POST /api/documents/{document_id}/attachments` is multipart with one or more
`attachments` fields. Add `?overwrite=true` to replace an existing filename.

### Delete a document

`DELETE /api/documents/{document_id}` removes its manifest, markdown body, and
all linked attachments.

### Reclassify legacy documents

`POST /api/migrate/reclassify?dry_run=true` applies heuristics to legacy
documents and infers a `function` and `provenance.source_type`. Use `dry_run=true`
to preview changes; omit it to apply them. This is a one-time operator tool,
not part of normal capture flow.

## Capture-agent contract

Mneme does not fetch or scrape sources. A capture agent:

1. retrieves and verifies the source,
2. transforms its body into markdown,
3. downloads the source's relevant media,
4. uploads the markdown, provenance, project association, context IDs, and media to Mneme.

Agents must set `provenance.source_type` and `provenance.agent_id`. For
`research` documents, agents must provide a real `source_url`. Human operators
may use `human_capture` and omit the URL.

The canonical write secret lives only in
`/mnt/temp/config/mneme/.env` on the press as `MNEME_WRITE_TOKEN`. Write agents
may fetch it over `ssh dev-ubuntu`; read-only consumers such as Bandit must
never receive it.