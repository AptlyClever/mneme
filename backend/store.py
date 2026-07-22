"""Filesystem document vault for Mneme.

The vault directory is the source of truth. Each document lives in its own
directory named by a 32-char hex id:

    <vault_root>/
        <doc_id>/
            manifest.json      # metadata + attachment index
            body.md            # markdown body
            attachments/       # binary attachments
                <filename>

Writes are atomic: files are written to a temp path and ``os.replace``d into
place; new documents are staged in ``<vault_root>/.staging`` and renamed in.
All ids and filenames are validated before touching the filesystem so no
request-supplied value can escape the vault.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

MANIFEST_NAME = "manifest.json"
BODY_NAME = "body.md"
ATTACHMENTS_DIR = "attachments"
STAGING_DIR = ".staging"
MANIFEST_SCHEMA_VERSION = 1

# Constrained document-purpose discriminator. Existing manifests that omit the
# field are treated as research (the historical default) without rewriting disk.
DOCUMENT_FUNCTIONS = frozenset({"research", "plan"})
DEFAULT_DOCUMENT_FUNCTION = "research"

_METADATA_KEYS = (
    "project_id",
    "title",
    "function",
    "author",
    "publisher",
    "published_at",
    "source_url",
    "captured_at",
    "tags",
)


def effective_document_function(manifest: dict[str, Any]) -> str:
    """Return the document function, defaulting missing values to research."""
    value = manifest.get("function")
    if value in DOCUMENT_FUNCTIONS:
        return value
    return DEFAULT_DOCUMENT_FUNCTION

_DOC_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ ()\[\]-]*$")
# Windows-reserved device names (case-insensitive, with or without extension).
_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_MAX_FILENAME_LEN = 255


class StoreError(Exception):
    """Base class for vault errors."""


class DocumentNotFound(StoreError):
    def __init__(self, doc_id: str) -> None:
        super().__init__(f"document not found: {doc_id}")
        self.doc_id = doc_id


class AttachmentNotFound(StoreError):
    def __init__(self, doc_id: str, filename: str) -> None:
        super().__init__(f"attachment not found: {filename} (document {doc_id})")
        self.doc_id = doc_id
        self.filename = filename


class InvalidDocumentId(StoreError):
    def __init__(self, doc_id: str) -> None:
        super().__init__(f"invalid document id: {doc_id!r}")
        self.doc_id = doc_id


class InvalidFilename(StoreError):
    def __init__(self, filename: str, reason: str = "unsafe filename") -> None:
        super().__init__(f"{reason}: {filename!r}")
        self.filename = filename


class AttachmentAlreadyExists(StoreError):
    def __init__(self, doc_id: str, filename: str) -> None:
        super().__init__(f"attachment already exists: {filename} (document {doc_id})")
        self.doc_id = doc_id
        self.filename = filename


@dataclass(frozen=True)
class AttachmentIn:
    """An attachment payload accepted by the store."""

    filename: str
    content: bytes
    content_type: Optional[str] = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_doc_id() -> str:
    return uuid.uuid4().hex


def validate_doc_id(doc_id: str) -> str:
    if not isinstance(doc_id, str) or not _DOC_ID_RE.fullmatch(doc_id):
        raise InvalidDocumentId(doc_id)
    return doc_id


def sanitize_filename(filename: str) -> str:
    """Validate an attachment filename, rejecting anything path-like.

    Only the basename is considered; any directory component, traversal
    sequence, control character, or reserved name is rejected outright
    rather than silently rewritten, so callers always know exactly what
    name was stored.
    """
    if not isinstance(filename, str) or not filename:
        raise InvalidFilename(str(filename), "empty filename")
    name = unicodedata.normalize("NFC", filename)
    if len(name) > _MAX_FILENAME_LEN:
        raise InvalidFilename(filename, "filename too long")
    if "/" in name or "\\" in name or "\x00" in name:
        raise InvalidFilename(filename, "filename must not contain path separators")
    if name in (".", "..") or name != name.strip():
        raise InvalidFilename(filename, "unsafe filename")
    if not _SAFE_FILENAME_RE.fullmatch(name):
        raise InvalidFilename(
            filename,
            "filename must start with an alphanumeric character and contain "
            "only letters, digits, spaces, and . _ - ( ) [ ]",
        )
    stem = name.split(".", 1)[0].lower()
    if stem in _RESERVED_NAMES:
        raise InvalidFilename(filename, "reserved filename")
    return name


def guess_content_type(filename: str, provided: Optional[str] = None) -> str:
    if provided and provided.strip() and provided.strip().lower() != "application/octet-stream":
        return provided.strip()
    guessed, _ = mimetypes.guess_type(filename)
    if guessed:
        return guessed
    return (provided or "").strip() or "application/octet-stream"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes to *path* atomically via a temp file in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    _atomic_write_bytes(path, data.encode("utf-8"))


class DocumentStore:
    """Filesystem-backed document vault."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._staging = self.root / STAGING_DIR
        self._staging.mkdir(parents=True, exist_ok=True)

    # -- path helpers -----------------------------------------------------

    def doc_dir(self, doc_id: str) -> Path:
        validate_doc_id(doc_id)
        path = (self.root / doc_id).resolve()
        # Defense in depth: the regex already guarantees containment.
        if path.parent != self.root:
            raise InvalidDocumentId(doc_id)
        return path

    def _require_doc_dir(self, doc_id: str) -> Path:
        path = self.doc_dir(doc_id)
        if not (path / MANIFEST_NAME).is_file():
            raise DocumentNotFound(doc_id)
        return path

    def attachment_path(self, doc_id: str, filename: str) -> Path:
        doc = self._require_doc_dir(doc_id)
        name = sanitize_filename(filename)
        path = (doc / ATTACHMENTS_DIR / name).resolve()
        if path.parent != (doc / ATTACHMENTS_DIR).resolve():
            raise InvalidFilename(filename)
        return path

    # -- manifest / body I/O ----------------------------------------------

    def read_manifest(self, doc_id: str) -> dict[str, Any]:
        doc = self._require_doc_dir(doc_id)
        try:
            raw = (doc / MANIFEST_NAME).read_text(encoding="utf-8")
        except FileNotFoundError:
            raise DocumentNotFound(doc_id) from None
        return json.loads(raw)

    def read_body(self, doc_id: str) -> str:
        doc = self._require_doc_dir(doc_id)
        body = doc / BODY_NAME
        if not body.is_file():
            return ""
        return body.read_text(encoding="utf-8")

    # -- write operations ---------------------------------------------------

    def create_document(
        self,
        metadata: dict[str, Any],
        body: str,
        attachments: Iterable[AttachmentIn] = (),
    ) -> dict[str, Any]:
        """Create a document atomically and return its manifest.

        The document is fully assembled in a staging directory and only
        renamed into the vault once every file is on disk, so readers never
        observe a partially written document.
        """
        doc_id = new_doc_id()
        now = utc_now_iso()

        attachment_entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        prepared: list[tuple[str, bytes]] = []
        for att in attachments:
            name = sanitize_filename(att.filename)
            if name in seen:
                raise AttachmentAlreadyExists(doc_id, name)
            seen.add(name)
            prepared.append((name, att.content))
            attachment_entries.append(
                {
                    "filename": name,
                    "content_type": guess_content_type(name, att.content_type),
                    "size": len(att.content),
                    "sha256": sha256_hex(att.content),
                }
            )

        manifest: dict[str, Any] = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "id": doc_id,
            "created_at": now,
            "updated_at": now,
            "attachments": attachment_entries,
            **{k: metadata.get(k) for k in _METADATA_KEYS},
        }
        if not manifest.get("captured_at"):
            manifest["captured_at"] = now
        manifest["function"] = effective_document_function(manifest)
        manifest["tags"] = list(manifest.get("tags") or [])

        staging = self._staging / doc_id
        staging.mkdir(parents=True)
        try:
            _atomic_write_bytes(staging / BODY_NAME, body.encode("utf-8"))
            att_dir = staging / ATTACHMENTS_DIR
            att_dir.mkdir()
            for name, content in prepared:
                _atomic_write_bytes(att_dir / name, content)
            _atomic_write_json(staging / MANIFEST_NAME, manifest)
            os.rename(staging, self.root / doc_id)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return manifest

    def update_document(
        self,
        doc_id: str,
        metadata: Optional[dict[str, Any]] = None,
        body: Optional[str] = None,
    ) -> dict[str, Any]:
        """Patch metadata fields and/or replace the body. Returns the manifest."""
        doc = self._require_doc_dir(doc_id)
        manifest = self.read_manifest(doc_id)
        if metadata:
            for key in _METADATA_KEYS:
                if key in metadata:
                    manifest[key] = metadata[key]
            manifest["function"] = effective_document_function(manifest)
            manifest["tags"] = list(manifest.get("tags") or [])
        if body is not None:
            _atomic_write_bytes(doc / BODY_NAME, body.encode("utf-8"))
        manifest["updated_at"] = utc_now_iso()
        _atomic_write_json(doc / MANIFEST_NAME, manifest)
        return manifest

    def add_attachments(
        self,
        doc_id: str,
        attachments: Iterable[AttachmentIn],
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Add attachments to an existing document. Returns the manifest."""
        doc = self._require_doc_dir(doc_id)
        manifest = self.read_manifest(doc_id)
        entries: list[dict[str, Any]] = list(manifest.get("attachments") or [])
        existing = {e["filename"]: e for e in entries}

        att_dir = doc / ATTACHMENTS_DIR
        att_dir.mkdir(exist_ok=True)
        batch_names: set[str] = set()
        for att in attachments:
            name = sanitize_filename(att.filename)
            if name in batch_names or (name in existing and not overwrite):
                raise AttachmentAlreadyExists(doc_id, name)
            batch_names.add(name)
            _atomic_write_bytes(att_dir / name, att.content)
            entry = {
                "filename": name,
                "content_type": guess_content_type(name, att.content_type),
                "size": len(att.content),
                "sha256": sha256_hex(att.content),
            }
            if name in existing:
                existing[name].update(entry)
            else:
                entries.append(entry)
                existing[name] = entry

        manifest["attachments"] = entries
        manifest["updated_at"] = utc_now_iso()
        _atomic_write_json(doc / MANIFEST_NAME, manifest)
        return manifest

    def get_attachment(self, doc_id: str, filename: str) -> tuple[Path, dict[str, Any]]:
        """Return the on-disk path and manifest entry for an attachment."""
        manifest = self.read_manifest(doc_id)
        name = sanitize_filename(filename)
        entry = next(
            (e for e in manifest.get("attachments") or [] if e["filename"] == name),
            None,
        )
        path = self.attachment_path(doc_id, name)
        if entry is None or not path.is_file():
            raise AttachmentNotFound(doc_id, filename)
        return path, entry

    def delete_document(self, doc_id: str) -> None:
        doc = self._require_doc_dir(doc_id)
        # Rename out of the vault first so readers never see a half-deleted doc.
        trash = self._staging / f"delete-{doc_id}-{uuid.uuid4().hex}"
        os.rename(doc, trash)
        shutil.rmtree(trash, ignore_errors=True)

    # -- listing / search ---------------------------------------------------

    def iter_doc_ids(self) -> Iterable[str]:
        for entry in os.scandir(self.root):
            if entry.is_dir() and _DOC_ID_RE.fullmatch(entry.name):
                yield entry.name

    def search(
        self,
        query: Optional[str] = None,
        project_id: Optional[str] = None,
        tag: Optional[str] = None,
        function: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[int, list[dict[str, Any]]]:
        """Search title/body/tags, optionally filtered by project, tag, function.

        Returns ``(total_matches, page_of_manifests)`` ordered by
        ``captured_at`` descending. Manifests missing ``function`` match
        ``research``.
        """
        needle = (query or "").strip().lower()
        matches: list[dict[str, Any]] = []
        for doc_id in self.iter_doc_ids():
            try:
                manifest = self.read_manifest(doc_id)
            except (DocumentNotFound, json.JSONDecodeError):
                continue
            if project_id is not None and manifest.get("project_id") != project_id:
                continue
            if (
                function is not None
                and effective_document_function(manifest) != function
            ):
                continue
            tags = [str(t) for t in (manifest.get("tags") or [])]
            if tag is not None and tag not in tags:
                continue
            if needle:
                haystack = " ".join(
                    filter(None, [str(manifest.get("title") or ""), *tags])
                ).lower()
                if needle not in haystack and needle not in self.read_body(doc_id).lower():
                    continue
            matches.append(manifest)

        matches.sort(key=lambda m: str(m.get("captured_at") or ""), reverse=True)
        total = len(matches)
        return total, matches[offset : offset + limit]
