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
import os
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

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
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import __version__
from .store import (
    AttachmentAlreadyExists,
    AttachmentIn,
    AttachmentNotFound,
    DocumentNotFound,
    DocumentStore,
    InvalidDocumentId,
    InvalidFilename,
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
    return Settings(
        vault_root=Path(os.environ.get("MNEME_VAULT_ROOT", DEFAULT_VAULT_ROOT)),
        write_token=token,
        max_upload_bytes=max_upload,
    )


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


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


class DocumentMetadata(BaseModel):
    """Metadata supplied when creating a document."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(pattern=_PROJECT_ID_PATTERN)
    title: str = Field(min_length=1, max_length=500)
    author: Optional[str] = Field(default=None, max_length=500)
    publisher: Optional[str] = Field(default=None, max_length=500)
    published_at: Optional[datetime] = None
    source_url: Optional[str] = Field(default=None, max_length=2048)
    captured_at: Optional[datetime] = None
    tags: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("title", "author", "publisher")
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


class DocumentPatch(BaseModel):
    """Partial update: only fields present in the request are applied."""

    model_config = ConfigDict(extra="forbid")

    project_id: Optional[str] = Field(default=None, pattern=_PROJECT_ID_PATTERN)
    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    author: Optional[str] = Field(default=None, max_length=500)
    publisher: Optional[str] = Field(default=None, max_length=500)
    published_at: Optional[datetime] = None
    source_url: Optional[str] = Field(default=None, max_length=2048)
    captured_at: Optional[datetime] = None
    tags: Optional[list[str]] = Field(default=None, max_length=64)
    body: Optional[str] = None

    @field_validator("source_url")
    @classmethod
    def _check_url(cls, value: Optional[str]) -> Optional[str]:
        return _validate_source_url(value)

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, tags: Optional[list[str]]) -> Optional[list[str]]:
        return None if tags is None else _normalize_tags(tags)


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
    author: Optional[str] = None
    publisher: Optional[str] = None
    published_at: Optional[str] = None
    source_url: Optional[str] = None
    captured_at: Optional[str] = None
    created_at: str
    updated_at: str
    tags: list[str] = Field(default_factory=list)
    attachments: list[AttachmentInfo] = Field(default_factory=list)


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


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_store(request: Request) -> DocumentStore:
    return request.app.state.store


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


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
        description="Durable, multi-project research document vault.",
    )
    app.state.settings = settings
    app.state.store = DocumentStore(settings.vault_root)
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
            documents=sum(1 for _ in store.iter_doc_ids()),
            writes_enabled=bool(cfg.write_token),
        )

    @router.get("/documents", response_model=DocumentList)
    def list_documents(
        store: Annotated[DocumentStore, Depends(get_store)],
        q: Optional[str] = Query(default=None, max_length=500),
        project_id: Optional[str] = Query(default=None, max_length=128),
        tag: Optional[str] = Query(default=None, max_length=100),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> DocumentList:
        total, manifests = store.search(
            query=q, project_id=project_id, tag=tag, limit=limit, offset=offset
        )
        return DocumentList(
            total=total,
            limit=limit,
            offset=offset,
            items=[DocumentSummary.model_validate(m) for m in manifests],
        )

    @router.get("/documents/{doc_id}", response_model=DocumentDetail)
    def get_document(
        doc_id: str,
        store: Annotated[DocumentStore, Depends(get_store)],
    ) -> DocumentDetail:
        manifest = store.read_manifest(doc_id)
        return DocumentDetail.model_validate(
            {**manifest, "body": store.read_body(doc_id)}
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

    # -- writes (token-gated) ----------------------------------------------

    @router.post(
        "/documents",
        response_model=DocumentDetail,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_write_token)],
    )
    async def create_document(
        store: Annotated[DocumentStore, Depends(get_store)],
        cfg: Annotated[Settings, Depends(get_settings)],
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
        return DocumentDetail.model_validate({**manifest, "body": body})

    @router.patch(
        "/documents/{doc_id}",
        response_model=DocumentDetail,
        dependencies=[Depends(require_write_token)],
    )
    def patch_document(
        doc_id: str,
        patch: DocumentPatch,
        store: Annotated[DocumentStore, Depends(get_store)],
        cfg: Annotated[Settings, Depends(get_settings)],
    ) -> DocumentDetail:
        provided = patch.model_dump(mode="json", include=patch.model_fields_set)
        body = provided.pop("body", None)
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
        return DocumentDetail.model_validate(
            {**manifest, "body": store.read_body(doc_id)}
        )

    @router.post(
        "/documents/{doc_id}/attachments",
        response_model=DocumentDetail,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_write_token)],
    )
    async def add_attachments(
        doc_id: str,
        store: Annotated[DocumentStore, Depends(get_store)],
        cfg: Annotated[Settings, Depends(get_settings)],
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
        return DocumentDetail.model_validate(
            {**manifest, "body": store.read_body(doc_id)}
        )

    @router.delete(
        "/documents/{doc_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_write_token)],
    )
    def delete_document(
        doc_id: str,
        store: Annotated[DocumentStore, Depends(get_store)],
    ) -> Response:
        store.delete_document(doc_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

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
