# Mneme API

Mneme stores markdown research documents with linked attachments. Reads are
open to LAN consumers. Writes require `Authorization: Bearer
<MNEME_WRITE_TOKEN>` and otherwise return HTTP 403:

```json
{"detail":"mneme_read_only"}
```

Base URL on the press: `http://192.168.68.93:8790/api`

## Document shape

```json
{
  "id": "0db12d50fc9e4a98922162623be77220",
  "project_id": "bandit",
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

## Open reads

- `GET /api/health`
- `GET /api/documents?q=&project_id=&tag=&limit=50&offset=0`
- `GET /api/documents/{document_id}` returns metadata plus the markdown `body`
- `GET /api/documents/{document_id}/attachments/{filename}` returns bytes with
  the stored MIME type and checksum ETag

Search is a case-insensitive substring match over title, body, author,
publisher, source URL, project, and tags. `project_id` and `tag` add exact
filters. Limits are 1–500.

## Token-gated writes

### Create a document and attachments in one request

`POST /api/documents` is multipart:

- `metadata`: JSON string containing `project_id`, `title`, and optional
  `author`, `publisher`, `published_at`, `source_url`, `captured_at`, `tags`
- `body`: markdown string
- `attachments`: zero or more files

```bash
curl -sS -X POST "http://192.168.68.93:8790/api/documents" \
  -H "Authorization: Bearer $MNEME_WRITE_TOKEN" \
  -F 'metadata={"project_id":"bandit","title":"Slot development trends","publisher":"1spin4win","source_url":"https://example.com/article","tags":["slots","market-research"]}' \
  -F 'body=<./article.md' \
  -F 'attachments=@./market-chart.png'
```

### Patch a document

`PATCH /api/documents/{document_id}` accepts JSON with any document metadata
field and/or `body`.

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

The canonical write secret lives only in
`/mnt/temp/config/mneme/.env` on the press as `MNEME_WRITE_TOKEN`. Write agents
may fetch it over `ssh dev-ubuntu`; read-only consumers such as Bandit must
never receive it.

