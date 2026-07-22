# Mneme API

Mneme stores markdown documents with linked attachments. Reads are open to LAN
consumers. Writes require `Authorization: Bearer <MNEME_WRITE_TOKEN>` and
otherwise return HTTP 403:

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
  ]
}
```

### Document function

`function` is a constrained purpose discriminator:

| Value | Use |
| --- | --- |
| `research` | Externally captured research (default) |
| `plan` | Internally authored forward-looking plan |

- Accepted on create; returned in every document response; persisted in
  `manifest.json`; preserved across patch/update unless explicitly changed.
- Exact list filter: `GET /api/documents?function=plan`
- Composes with other filters: `GET /api/documents?project_id=axiom&function=plan`
- Unknown values are rejected. Missing values on existing vault documents are
  treated as `research` without rewriting disk.
- Tags remain free-form topic labels and are **not** a substitute for
  `function`.

### Provenance by function

| Function | `source_url` |
| --- | --- |
| `research` | Prefer a real external http(s) URL when the artifact was captured from outside |
| `plan` | May be `null` — internally authored plans have no external source |

`source_url`, when present, must be `http://` or `https://`. Omitting it for
research does not fail validation today, but capture agents should still supply
provenance for research documents. Do not invent a `source_url` for authored
plans.

## Open reads

- `GET /api/health`
- `GET /api/documents?q=&project_id=&tag=&function=&limit=50&offset=0`
- `GET /api/documents/{document_id}` returns metadata plus the markdown `body`
- `GET /api/documents/{document_id}/attachments/{filename}` returns bytes with
  the stored MIME type and checksum ETag

Search is a case-insensitive substring match over title, body, and tags.
`project_id`, `tag`, and `function` add exact filters. Limits are 1–500.

## Token-gated writes

### Create a document and attachments in one request

`POST /api/documents` is multipart:

- `metadata`: JSON string containing `project_id`, `title`, and optional
  `function`, `author`, `publisher`, `published_at`, `source_url`,
  `captured_at`, `tags`
- `body`: markdown string
- `attachments`: zero or more files

```bash
curl -sS -X POST "http://192.168.68.93:8790/api/documents" \
  -H "Authorization: Bearer $MNEME_WRITE_TOKEN" \
  -F 'metadata={"project_id":"bandit","function":"research","title":"Slot development trends","publisher":"1spin4win","source_url":"https://example.com/article","tags":["slots","market-research"]}' \
  -F 'body=<./article.md' \
  -F 'attachments=@./market-chart.png'
```

Authored plan example (no external source):

```bash
curl -sS -X POST "http://192.168.68.93:8790/api/documents" \
  -H "Authorization: Bearer $MNEME_WRITE_TOKEN" \
  -F 'metadata={"project_id":"axiom","function":"plan","title":"Example plan","author":"Control Alt","publisher":"Control Alt","source_url":null,"tags":["integration-plan"]}' \
  -F 'body=<./plan.md'
```

### Patch a document

`PATCH /api/documents/{document_id}` accepts JSON with any document metadata
field (including `function`) and/or `body`.

### Add attachments

`POST /api/documents/{document_id}/attachments` is multipart with one or more
`attachments` fields. Add `?overwrite=true` to replace an existing filename.

### Delete a document

`DELETE /api/documents/{document_id}` removes its manifest, markdown body, and
all linked attachments.

## Capture-agent contract

Mneme does not fetch or scrape sources. A capture agent:

1. retrieves and verifies the source,
2. transforms its body into markdown,
3. downloads the source's relevant media,
4. uploads the markdown, provenance, project association, and media to Mneme.

For internally authored `plan` documents, skip inventing an external source and
set `function` to `plan` explicitly.

The canonical write secret lives only in
`/mnt/temp/config/mneme/.env` on the press as `MNEME_WRITE_TOKEN`. Write agents
may fetch it over `ssh dev-ubuntu`; read-only consumers such as Bandit must
never receive it.
