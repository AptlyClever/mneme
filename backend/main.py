"""Mneme HTTP API.

FastAPI layer over :class:`backend.store.DocumentStore`.

* Reads are open (unauthenticated).
* Writes require ``Authorization: Bearer <MNEME_WRITE_TOKEN>`` and fail
  closed: if no token is configured, every write is rejected.
* ``MNEME_VAULT_ROOT`` selects the vault directory (default ``./data/vault``).
* ``MNEME_MAX_UPLOAD_BYTES`` caps the combined size of body + attachments
  per write request (default 25 MiB).

Mneme never fetches URLs; ``source_url`` is stored as provenance only.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Optional

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import __version__
from .audit import AuditLogger
from .store import (
    DEFAULT_DOCUMENT_FUNCTION,
    AttachmentAlreadyExists,
    AttachmentCorrupted,
    AttachmentIn,
    AttachmentNotFound,
    DocumentNotFound,
    DocumentStore,
    InvalidDocumentId,
    InvalidFilename,
    effective_document_function,
    effective_provenance,
    is_agent_source,
)

DEFAULT_VAULT_ROOT = "./data/vault"
DEFAULT_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
_READ_CHUNK = 1024 * 1024

_PROJECT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    vault_root: Path
    write_token: Optional[str]
    max_upload_bytes: int
    hephaestus_base_url: Optional[str] = None
    known_projects: frozenset[str] = frozenset()


def _load_known_projects(base_url: Optional[str]) -> frozenset[str]:
    """Fetch the Hephaestus project list to use as a canonical vocabulary.

    Fail-soft: if Hephaestus is unreachable or not configured, return an empty set.
    """
    if not base_url:
        return frozenset()
    import urllib.error
    import urllib.request

    logger = logging.getLogger(__name__)
    try:
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/api/projects",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        projects = data.get("projects") or data.get("items") or []
        return frozenset(str(p.get("id")) for p in projects if p.get("id"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        logger.warning("Could not load Hephaestus project list: %s", exc)
        return frozenset()


def _warn_unknown_project(project_id: str, known_projects: frozenset[str]) -> None:
    if known_projects and project_id not in known_projects:
        logging.getLogger(__name__).warning(
            "project_id %r is not in the Hephaestus project vocabulary", project_id
        )


def _infer_reclassification(manifest: dict[str, Any]) -> dict[str, Any]:
    """Infer a new function and provenance.source_type for a legacy document."""
    function = manifest.get("function")
    provenance = dict(manifest.get("provenance") or {})
    changes: dict[str, Any] = {}

    # Plans stay plans and are human-authored.
    if function == "plan":
        if provenance.get("source_type") in (None, "unknown"):
            provenance["source_type"] = "human_capture"
            changes["provenance"] = provenance
        return changes

    title = str(manifest.get("title") or "").lower()
    author = str(manifest.get("author") or "").lower()
    source_url = manifest.get("source_url") or provenance.get("source_url")
    attachments = manifest.get("attachments") or []
    has_images = any(
        a.get("filename", "").lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
        for a in attachments
    )

    new_function: Optional[str] = None
    source_type: Optional[str] = None

    if source_url:
        source_type = "github_issue" if "github.com" in source_url else "external_document"
        new_function = "research"
    elif has_images or any(kw in title for kw in ("reference", "photo", "image", "board", "dobson")):
        source_type = "human_capture"
        new_function = "reference"
    elif any(kw in title for kw in ("decision", "eval", "locked", "identity packet", "directive", "mystery")):
        source_type = "human_capture"
        new_function = "decision"
    elif any(kw in title for kw in ("vision", "platform", "strategy", "insight", "assessment")):
        source_type = "human_capture"
        new_function = "insight"
    elif any(kw in author for kw in ("agent", "cursor", "chatgpt", "openai", "mara", "llm")):
        source_type = "agent_synthesis"
        new_function = "synthesis"
    else:
        # Conservative fallback: human-captured research so existing docs remain valid.
        source_type = "human_capture"
        new_function = "research"

    if manifest.get("function") != new_function and new_function is not None:
        changes["function"] = new_function

    if provenance.get("source_type") in (None, "unknown"):
        provenance["source_type"] = source_type
    if manifest.get("source_url") and not provenance.get("source_url"):
        provenance["source_url"] = manifest["source_url"]

    if changes.get("function") or provenance != (manifest.get("provenance") or {}):
        changes["provenance"] = provenance

    return changes


def load_settings() -> Settings:
    raw_max = os.environ.get("MNEME_MAX_UPLOAD_BYTES", "").strip()
    try:
        max_upload = int(raw_max) if raw_max else DEFAULT_MAX_UPLOAD_BYTES
    except ValueError:
        raise RuntimeError(
            f"MNEME_MAX_UPLOAD_BYTES must be an integer, got {raw_max!r}"
        ) from None
    if max_upload <= 0:
        raise RuntimeError("MNEME_MAX_UPLOAD_BYTES must be positive")
    token = os.environ.get("MNEME_WRITE_TOKEN", "").strip() or None
    hephaestus_url = os.environ.get("HEPHAESTUS_BASE_URL", "").strip() or None
    known_projects = _load_known_projects(hephaestus_url)
    return Settings(
        vault_root=Path(os.environ.get("MNEME_VAULT_ROOT", DEFAULT_VAULT_ROOT)),
        write_token=token,
        max_upload_bytes=max_upload,
        hephaestus_base_url=hephaestus_url,
        known_projects=known_projects,
    )


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


DocumentFunction = Literal[
    "research",
    "plan",
    "synthesis",
    "decision",
    "reference",
    "insight",
    "preference",
]

SourceType = Literal[
    "human_capture",
    "agent_synthesis",
    "web_page",
    "github_issue",
    "external_document",
    "unknown",
]

ContextType = Literal["cfd", "cue", "project"]

DocumentStatus = Literal["active", "draft", "superseded", "archived"]


def _validate_source_url(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if not (value.startswith("http://") or value.startswith("https://")):
        raise ValueError("source_url must be an http(s) URL")
    return value


def _normalize_tags(tags: list[str]) -> list[str]:
    cleaned: list[str] = []
    for tag in tags:
        tag = tag.strip()
        if not tag:
            continue
        if len(tag) > 100:
            raise ValueError("tags must be at most 100 characters")
        if tag not in cleaned:
            cleaned.append(tag)
    return cleaned


def _manifest_for_response(manifest: dict) -> dict:
    """Ensure responses always expose an effective document function and provenance."""
    response = {**manifest, "function": effective_document_function(manifest)}
    provenance = effective_provenance(manifest)
    if provenance.get("source_url") and not response.get("source_url"):
        response["source_url"] = provenance["source_url"]
    response["provenance"] = provenance
    return response


class Provenance(BaseModel):
    """Structured provenance for a document."""

    model_config = ConfigDict(extra="forbid")

    source_type: SourceType = "unknown"
    agent_id: Optional[str] = Field(default=None, max_length=128)
    session_id: Optional[str] = Field(default=None, max_length=256)
    source_url: Optional[str] = Field(default=None, max_length=2048)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @field_validator("source_url")
    @classmethod
    def _check_url(cls, value: Optional[str]) -> Optional[str]:
        return _validate_source_url(value)

    @field_validator("agent_id", "session_id")
    @classmethod
    def _strip_text(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if isinstance(value, str) else value


class ContextId(BaseModel):
    """Reference to a Control Alt context (CFD, Cue, project)."""

    model_config = ConfigDict(extra="forbid")

    type: ContextType
    id: str = Field(min_length=1, max_length=256)

    @field_validator("id")
    @classmethod
    def _strip_id(cls, value: str) -> str:
        return value.strip()


class Relationships(BaseModel):
    """Cross-document relationships."""

    model_config = ConfigDict(extra="forbid")

    parent_id: Optional[str] = None
    supersedes: list[str] = Field(default_factory=list)
    related_ids: list[str] = Field(default_factory=list)


class DocumentMetadata(BaseModel):
    """Metadata supplied when creating a document."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(pattern=_PROJECT_ID_PATTERN)
    title: str = Field(min_length=1, max_length=500)
    function: DocumentFunction
    author: Optional[str] = Field(default=None, max_length=500)
    publisher: Optional[str] = Field(default=None, max_length=500)
    published_at: Optional[datetime] = None
    source_url: Optional[str] = Field(default=None, max_length=2048)
    captured_at: Optional[datetime] = None
    tags: list[str] = Field(default_factory=list, max_length=64)
    provenance: Optional[Provenance] = None
    context_ids: list[ContextId] = Field(default_factory=list)
    relationships: Optional[Relationships] = None
    status: DocumentStatus = "active"
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = Field(default=None, max_length=128)

    @field_validator("title", "author", "publisher", "reviewed_by")
    @classmethod
    def _strip_text(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if isinstance(value, str) else value

    @field_validator("source_url")
    @classmethod
    def _check_url(cls, value: Optional[str]) -> Optional[str]:
        return _validate_source_url(value)

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, tags: list[str]) -> list[str]:
        return _normalize_tags(tags)

    @model_validator(mode="after")
    def _sync_provenance_source_url(self) -> "DocumentMetadata":
        # Keep top-level source_url and provenance.source_url in sync.
        provenance = self.provenance
        top_url = self.source_url
        if provenance is None:
            self.provenance = Provenance(source_url=top_url)
        elif provenance.source_url is None and top_url is not None:
            provenance.source_url = top_url
        elif top_url is None and provenance.source_url is not None:
            self.source_url = provenance.source_url
        return self

    @model_validator(mode="after")
    def _require_source_url_for_agent_research(self) -> "DocumentMetadata":
        if self.function == "research":
            provenance = self.provenance or Provenance()
            if provenance.source_type != "human_capture":
                url = provenance.source_url or self.source_url
                if not url:
                    raise ValueError(
                        "research documents from non-human sources require a source_url"
                    )
        return self


class DocumentPatch(BaseModel):
    """Partial update: only fields present in the request are applied."""

    model_config = ConfigDict(extra="forbid")

    project_id: Optional[str] = Field(default=None, pattern=_PROJECT_ID_PATTERN)
    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    function: Optional[DocumentFunction] = None
    author: Optional[str] = Field(default=None, max_length=500)
    publisher: Optional[str] = Field(default=None, max_length=500)
    published_at: Optional[datetime] = None
    source_url: Optional[str] = Field(default=None, max_length=2048)
    captured_at: Optional[datetime] = None
    tags: Optional[list[str]] = Field(default=None, max_length=64)
    body: Optional[str] = None
    provenance: Optional[Provenance] = None
    context_ids: Optional[list[ContextId]] = None
    relationships: Optional[Relationships] = None
    status: Optional[DocumentStatus] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = Field(default=None, max_length=128)

    @field_validator("source_url")
    @classmethod
    def _check_url(cls, value: Optional[str]) -> Optional[str]:
        return _validate_source_url(value)

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, tags: Optional[list[str]]) -> Optional[list[str]]:
        return None if tags is None else _normalize_tags(tags)

    @field_validator("reviewed_by")
    @classmethod
    def _strip_text(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _sync_provenance_source_url(self) -> "DocumentPatch":
        if self.provenance is not None and self.source_url is not None:
            if self.provenance.source_url is None:
                self.provenance.source_url = self.source_url
            elif self.provenance.source_url != self.source_url:
                # If both are provided, provenance wins; keep top-level in sync.
                self.source_url = self.provenance.source_url
        return self

    @model_validator(mode="after")
    def _require_source_url_for_agent_research(self) -> "DocumentPatch":
        if self.function == "research":
            provenance = self.provenance or Provenance()
            if provenance.source_type != "human_capture":
                url = provenance.source_url or self.source_url
                if not url:
                    raise ValueError(
                        "research documents from non-human sources require a source_url"
                    )
        return self


class AttachmentInfo(BaseModel):
    filename: str
    content_type: str
    size: int
    sha256: str


class DocumentSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    project_id: Optional[str] = None
    title: Optional[str] = None
    function: DocumentFunction = DEFAULT_DOCUMENT_FUNCTION
    author: Optional[str] = None
    publisher: Optional[str] = None
    published_at: Optional[str] = None
    source_url: Optional[str] = None
    captured_at: Optional[str] = None
    created_at: str
    updated_at: str
    tags: list[str] = Field(default_factory=list)
    attachments: list[AttachmentInfo] = Field(default_factory=list)
    provenance: Optional[dict[str, Any]] = None
    context_ids: list[dict[str, Any]] = Field(default_factory=list)
    relationships: Optional[dict[str, Any]] = None
    status: DocumentStatus = "active"
    confidence: Optional[float] = None
    reviewed_at: Optional[str] = None
    reviewed_by: Optional[str] = None


class DocumentDetail(DocumentSummary):
    body: str = ""


class DocumentList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[DocumentSummary]


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    vault_root: str
    documents: int
    writes_enabled: bool


class AuditEntry(BaseModel):
    ts: str
    action: str
    doc_id: Optional[str] = None
    ip: Optional[str] = None
    user_agent: Optional[str] = None


class AuditList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AuditEntry]


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_store(request: Request) -> DocumentStore:
    return request.app.state.store


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit


def require_write_token(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Bearer-token gate for all mutating endpoints. Fails closed."""
    if not settings.write_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="mneme_read_only",
        )
    header = request.headers.get("authorization", "")
    scheme, _, credential = header.partition(" ")
    if scheme.lower() != "bearer" or not credential.strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="mneme_read_only",
        )
    if not secrets.compare_digest(
        credential.strip().encode("utf-8"), settings.write_token.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="mneme_read_only",
        )


async def _read_upload_capped(upload: UploadFile, remaining: int) -> bytes:
    """Read an upload without ever buffering more than the remaining budget."""
    chunks: list[bytes] = []
    read = 0
    while True:
        chunk = await upload.read(_READ_CHUNK)
        if not chunk:
            break
        read += len(chunk)
        if read > remaining:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="upload exceeds MNEME_MAX_UPLOAD_BYTES",
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _collect_attachments(
    files: list[UploadFile], budget: int
) -> list[AttachmentIn]:
    collected: list[AttachmentIn] = []
    remaining = budget
    for upload in files:
        if not upload.filename:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="every attachment must have a filename",
            )
        content = await _read_upload_capped(upload, remaining)
        remaining -= len(content)
        collected.append(
            AttachmentIn(
                filename=upload.filename,
                content=content,
                content_type=upload.content_type,
            )
        )
    return collected


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(
        title="Mneme",
        version=__version__,
        description="Durable cross-project Control Alt Knowledge store.",
    )
    app.state.settings = settings
    app.state.store = DocumentStore(settings.vault_root)
    app.state.audit = AuditLogger(settings.vault_root)
    router = APIRouter()

    @app.exception_handler(DocumentNotFound)
    @app.exception_handler(AttachmentNotFound)
    async def _not_found(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(InvalidDocumentId)
    @app.exception_handler(InvalidFilename)
    async def _bad_request(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(AttachmentAlreadyExists)
    async def _conflict(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(AttachmentCorrupted)
    async def _corrupted(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    # -- reads (open) -----------------------------------------------------

    @router.get("/health", response_model=HealthResponse)
    def health(
        store: Annotated[DocumentStore, Depends(get_store)],
        cfg: Annotated[Settings, Depends(get_settings)],
    ) -> HealthResponse:
        return HealthResponse(
            status="ok",
            service="mneme",
            version=__version__,
            vault_root=str(store.root),
            documents=store.doc_count,
            writes_enabled=bool(cfg.write_token),
        )

    @router.get("/audit", response_model=AuditList, dependencies=[Depends(require_write_token)])
    def list_audit(
        audit: Annotated[AuditLogger, Depends(get_audit)],
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> AuditList:
        total, entries = audit.read_entries(limit=limit, offset=offset)
        return AuditList(
            total=total,
            limit=limit,
            offset=offset,
            items=[AuditEntry.model_validate(e) for e in entries],
        )

    @router.get("/documents", response_model=DocumentList)
    def list_documents(
        store: Annotated[DocumentStore, Depends(get_store)],
        q: Optional[str] = Query(default=None, max_length=500),
        project_id: Optional[str] = Query(default=None, max_length=128),
        tag: Optional[str] = Query(default=None, max_length=100),
        function: Optional[DocumentFunction] = Query(default=None),
        context_type: Optional[str] = Query(default=None, max_length=64),
        context_id: Optional[str] = Query(default=None, max_length=256),
        status: Optional[DocumentStatus] = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> DocumentList:
        total, manifests = store.search(
            query=q,
            project_id=project_id,
            tag=tag,
            function=function,
            context_type=context_type,
            context_id=context_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        return DocumentList(
            total=total,
            limit=limit,
            offset=offset,
            items=[
                DocumentSummary.model_validate(_manifest_for_response(m))
                for m in manifests
            ],
        )

    @router.get("/documents/{doc_id}", response_model=DocumentDetail)
    def get_document(
        request: Request,
        doc_id: str,
        store: Annotated[DocumentStore, Depends(get_store)],
    ) -> Response:
        manifest = store.read_manifest(doc_id)
        etag = f'"{manifest.get("updated_at", "")}"'
        if_none_match = request.headers.get("if-none-match")
        if if_none_match and if_none_match == etag:
            return Response(status_code=status.HTTP_304_NOT_MODIFIED)
        detail = DocumentDetail.model_validate(
            {**_manifest_for_response(manifest), "body": store.read_body(doc_id)}
        )
        return JSONResponse(
            content=detail.model_dump(mode="json"),
            headers={"ETag": etag},
        )

    @router.get("/documents/{doc_id}/attachments/{filename}")
    def get_attachment(
        doc_id: str,
        filename: str,
        store: Annotated[DocumentStore, Depends(get_store)],
    ) -> FileResponse:
        path, entry = store.get_attachment(doc_id, filename)
        return FileResponse(
            path,
            media_type=entry["content_type"],
            filename=entry["filename"],
            headers={"ETag": f'"{entry["sha256"]}"'},
        )

    @router.get("/documents/{doc_id}/context", response_model=list[ContextId])
    def get_document_context(
        doc_id: str,
        store: Annotated[DocumentStore, Depends(get_store)],
    ) -> list[ContextId]:
        manifest = store.read_manifest(doc_id)
        return [ContextId.model_validate(c) for c in manifest.get("context_ids") or []]

    @router.get("/documents/{doc_id}/related", response_model=DocumentList)
    def get_related_documents(
        doc_id: str,
        store: Annotated[DocumentStore, Depends(get_store)],
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> DocumentList:
        manifest = store.read_manifest(doc_id)
        relationships = manifest.get("relationships") or {}
        related_ids: list[str] = []
        if relationships.get("parent_id"):
            related_ids.append(relationships["parent_id"])
        related_ids.extend(relationships.get("supersedes") or [])
        related_ids.extend(relationships.get("related_ids") or [])
        # Preserve order while removing duplicates.
        seen: set[str] = set()
        unique_ids: list[str] = []
        for rid in related_ids:
            if rid not in seen:
                seen.add(rid)
                unique_ids.append(rid)

        manifests: list[dict[str, Any]] = []
        for rid in unique_ids:
            try:
                manifests.append(store.read_manifest(rid))
            except DocumentNotFound:
                continue
        total = len(manifests)
        return DocumentList(
            total=total,
            limit=limit,
            offset=offset,
            items=[
                DocumentSummary.model_validate(_manifest_for_response(m))
                for m in manifests[offset : offset + limit]
            ],
        )

    # -- writes (token-gated) ----------------------------------------------

    @router.post(
        "/documents",
        response_model=DocumentDetail,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_write_token)],
    )
    async def create_document(
        request: Request,
        store: Annotated[DocumentStore, Depends(get_store)],
        cfg: Annotated[Settings, Depends(get_settings)],
        audit: Annotated[AuditLogger, Depends(get_audit)],
        metadata: Annotated[str, Form(description="DocumentMetadata as JSON")],
        body: Annotated[str, Form()] = "",
        attachments: Annotated[Optional[list[UploadFile]], File()] = None,
    ) -> DocumentDetail:
        try:
            meta = DocumentMetadata.model_validate(json.loads(metadata))
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"metadata is not valid JSON: {exc}",
            ) from None
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"invalid metadata: {exc}",
            ) from None

        _warn_unknown_project(meta.project_id, cfg.known_projects)

        body_bytes = len(body.encode("utf-8"))
        if body_bytes > cfg.max_upload_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="body exceeds MNEME_MAX_UPLOAD_BYTES",
            )
        files = await _collect_attachments(
            attachments or [], cfg.max_upload_bytes - body_bytes
        )
        manifest = store.create_document(
            meta.model_dump(mode="json"), body, files
        )
        audit.log(
            "create",
            doc_id=manifest["id"],
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        return DocumentDetail.model_validate(
            {**_manifest_for_response(manifest), "body": body}
        )

    @router.patch(
        "/documents/{doc_id}",
        response_model=DocumentDetail,
        dependencies=[Depends(require_write_token)],
    )
    def patch_document(
        request: Request,
        doc_id: str,
        patch: DocumentPatch,
        store: Annotated[DocumentStore, Depends(get_store)],
        cfg: Annotated[Settings, Depends(get_settings)],
        audit: Annotated[AuditLogger, Depends(get_audit)],
    ) -> DocumentDetail:
        provided = patch.model_dump(mode="json", include=patch.model_fields_set)
        body = provided.pop("body", None)
        if "project_id" in provided:
            _warn_unknown_project(provided["project_id"], cfg.known_projects)
        if body is not None and len(body.encode("utf-8")) > cfg.max_upload_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="body exceeds MNEME_MAX_UPLOAD_BYTES",
            )
        if not provided and body is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="patch must include at least one field",
            )
        manifest = store.update_document(doc_id, metadata=provided, body=body)
        audit.log(
            "update",
            doc_id=doc_id,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        return DocumentDetail.model_validate(
            {
                **_manifest_for_response(manifest),
                "body": store.read_body(doc_id),
            }
        )

    @router.post(
        "/documents/{doc_id}/attachments",
        response_model=DocumentDetail,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_write_token)],
    )
    async def add_attachments(
        request: Request,
        doc_id: str,
        store: Annotated[DocumentStore, Depends(get_store)],
        cfg: Annotated[Settings, Depends(get_settings)],
        audit: Annotated[AuditLogger, Depends(get_audit)],
        attachments: Annotated[list[UploadFile], File()],
        overwrite: bool = Query(default=False),
    ) -> DocumentDetail:
        if not attachments:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="at least one attachment is required",
            )
        files = await _collect_attachments(attachments, cfg.max_upload_bytes)
        manifest = store.add_attachments(doc_id, files, overwrite=overwrite)
        audit.log(
            "attach",
            doc_id=doc_id,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        return DocumentDetail.model_validate(
            {
                **_manifest_for_response(manifest),
                "body": store.read_body(doc_id),
            }
        )

    @router.delete(
        "/documents/{doc_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_write_token)],
    )
    def delete_document(
        request: Request,
        doc_id: str,
        store: Annotated[DocumentStore, Depends(get_store)],
        audit: Annotated[AuditLogger, Depends(get_audit)],
    ) -> Response:
        store.delete_document(doc_id)
        audit.log(
            "delete",
            doc_id=doc_id,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post(
        "/migrate/reclassify",
        dependencies=[Depends(require_write_token)],
    )
    def reclassify_documents(
        request: Request,
        store: Annotated[DocumentStore, Depends(get_store)],
        audit: Annotated[AuditLogger, Depends(get_audit)],
        dry_run: bool = Query(default=False),
    ) -> dict[str, Any]:
        """One-time migration to reclassify legacy documents using heuristics.

        Use ``?dry_run=true`` to preview changes without writing them.
        """
        changes: list[dict[str, Any]] = []
        for doc_id in store.iter_doc_ids():
            manifest = store.read_manifest(doc_id)
            updates = _infer_reclassification(manifest)
            if not updates:
                continue
            changes.append(
                {
                    "id": doc_id,
                    "title": manifest.get("title"),
                    "current_function": manifest.get("function"),
                    "changes": updates,
                }
            )
            if not dry_run:
                store.update_document(doc_id, metadata=updates)
                audit.log(
                    "reclassify",
                    doc_id=doc_id,
                    ip=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                )
        return {"dry_run": dry_run, "count": len(changes), "changes": changes}

    # `/api` is the canonical contract. Root aliases remain for simple LAN
    # clients and backward compatibility with the initial v1 test harness.
    app.include_router(router, prefix="/api")
    app.include_router(router, include_in_schema=False)
    if WEB_ROOT.is_dir():
        app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")

        @app.get("/", include_in_schema=False)
        def operator_ui() -> FileResponse:
            return FileResponse(WEB_ROOT / "index.html")
    return app


def __getattr__(name: str) -> FastAPI:
    # Lazily build the default app so importing this module (e.g. from tests)
    # has no filesystem side effects. `uvicorn backend.main:app` still works.
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
