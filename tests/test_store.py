"""Unit tests for the filesystem vault (backend.store)."""

import hashlib
import json
from pathlib import Path

import pytest

from backend.store import (
    ATTACHMENTS_DIR,
    BODY_NAME,
    MANIFEST_NAME,
    STAGING_DIR,
    AttachmentAlreadyExists,
    AttachmentIn,
    AttachmentNotFound,
    DocumentNotFound,
    DocumentStore,
    InvalidDocumentId,
    InvalidFilename,
    guess_content_type,
    sanitize_filename,
    validate_doc_id,
)


@pytest.fixture
def store(tmp_path: Path) -> DocumentStore:
    return DocumentStore(tmp_path / "vault")


def make_doc(store: DocumentStore, **overrides):
    metadata = {
        "project_id": "proj-a",
        "title": "Test Title",
        "author": "Ada",
        "publisher": "Journal",
        "published_at": "2024-01-01T00:00:00+00:00",
        "source_url": "https://example.com/paper",
        "captured_at": "2025-06-01T12:00:00+00:00",
        "tags": ["ml", "notes"],
    }
    metadata.update(overrides.pop("metadata", {}))
    body = overrides.pop("body", "# Heading\n\nbody text here")
    attachments = overrides.pop("attachments", [])
    return store.create_document(metadata, body, attachments)


# -- filename / id validation -------------------------------------------------


class TestSanitizeFilename:
    @pytest.mark.parametrize(
        "name",
        ["report.pdf", "data.tar.gz", "My Notes (v2).md", "a", "img_01[final].png"],
    )
    def test_accepts_safe_names(self, name):
        assert sanitize_filename(name) == name

    @pytest.mark.parametrize(
        "name",
        [
            "",
            ".",
            "..",
            "../evil.txt",
            "..\\evil.txt",
            "a/b.txt",
            "a\\b.txt",
            "evil\x00.txt",
            ".hidden",
            " leading-space.txt",
            "trailing-space.txt ",
            "CON",
            "con.txt",
            "COM1.log",
            "nul",
            "a" * 300,
        ],
    )
    def test_rejects_unsafe_names(self, name):
        with pytest.raises(InvalidFilename):
            sanitize_filename(name)


class TestDocId:
    def test_accepts_hex_ids(self):
        assert validate_doc_id("a" * 32) == "a" * 32

    @pytest.mark.parametrize(
        "doc_id", ["", "short", "A" * 32, "../../../etc/passwd", "a" * 33, "z" * 32]
    )
    def test_rejects_bad_ids(self, doc_id):
        with pytest.raises(InvalidDocumentId):
            validate_doc_id(doc_id)


def test_guess_content_type():
    assert guess_content_type("a.pdf") == "application/pdf"
    assert guess_content_type("a.bin") == "application/octet-stream"
    assert guess_content_type("a.bin", "image/png") == "image/png"
    # An explicit generic type falls back to extension-based guessing.
    assert guess_content_type("a.txt", "application/octet-stream") == "text/plain"


# -- create / read ------------------------------------------------------------


def test_create_document_layout_and_manifest(store: DocumentStore):
    payload = b"fake pdf bytes"
    manifest = make_doc(
        store,
        attachments=[AttachmentIn("paper.pdf", payload, "application/pdf")],
    )
    doc_id = manifest["id"]
    doc_dir = store.root / doc_id
    assert (doc_dir / MANIFEST_NAME).is_file()
    assert (doc_dir / BODY_NAME).is_file()
    assert (doc_dir / ATTACHMENTS_DIR / "paper.pdf").read_bytes() == payload

    on_disk = json.loads((doc_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert on_disk["project_id"] == "proj-a"
    assert on_disk["title"] == "Test Title"
    assert on_disk["function"] == "research"
    assert on_disk["tags"] == ["ml", "notes"]
    assert on_disk["source_url"] == "https://example.com/paper"
    (entry,) = on_disk["attachments"]
    assert entry["content_type"] == "application/pdf"
    assert entry["size"] == len(payload)
    assert entry["sha256"] == hashlib.sha256(payload).hexdigest()

    assert store.read_body(doc_id) == "# Heading\n\nbody text here"
    # Staging directory left clean after a successful create.
    assert list((store.root / STAGING_DIR).iterdir()) == []


def test_captured_at_defaults_to_now(store: DocumentStore):
    manifest = store.create_document({"project_id": "p", "title": "t"}, "b")
    assert manifest["captured_at"]
    assert manifest["captured_at"] == manifest["created_at"]
    assert manifest["function"] == "research"


def test_create_persists_plan_function(store: DocumentStore):
    manifest = store.create_document(
        {"project_id": "axiom", "title": "Plan", "function": "plan"},
        "# Plan body",
    )
    assert manifest["function"] == "plan"
    assert store.read_manifest(manifest["id"])["function"] == "plan"


def test_update_preserves_and_changes_function(store: DocumentStore):
    doc_id = make_doc(store)["id"]
    preserved = store.update_document(doc_id, metadata={"title": "Still research"})
    assert preserved["function"] == "research"
    changed = store.update_document(doc_id, metadata={"function": "plan"})
    assert changed["function"] == "plan"
    assert store.read_manifest(doc_id)["function"] == "plan"


def test_duplicate_attachment_names_rejected_on_create(store: DocumentStore):
    with pytest.raises(AttachmentAlreadyExists):
        make_doc(
            store,
            attachments=[
                AttachmentIn("a.txt", b"1", "text/plain"),
                AttachmentIn("a.txt", b"2", "text/plain"),
            ],
        )


def test_read_missing_document(store: DocumentStore):
    with pytest.raises(DocumentNotFound):
        store.read_manifest("f" * 32)


# -- update -------------------------------------------------------------------


def test_update_metadata_and_body(store: DocumentStore):
    doc_id = make_doc(store)["id"]
    before = store.read_manifest(doc_id)
    manifest = store.update_document(
        doc_id, metadata={"title": "New Title", "tags": ["x"]}, body="new body"
    )
    assert manifest["title"] == "New Title"
    assert manifest["tags"] == ["x"]
    assert manifest["author"] == "Ada"  # untouched fields preserved
    assert manifest["updated_at"] >= before["updated_at"]
    assert store.read_body(doc_id) == "new body"


def test_update_body_only_keeps_metadata(store: DocumentStore):
    doc_id = make_doc(store)["id"]
    store.update_document(doc_id, body="only body changed")
    manifest = store.read_manifest(doc_id)
    assert manifest["title"] == "Test Title"
    assert store.read_body(doc_id) == "only body changed"


# -- attachments --------------------------------------------------------------


def test_add_and_get_attachment(store: DocumentStore):
    doc_id = make_doc(store)["id"]
    data = b"\x89PNG fake image"
    store.add_attachments(doc_id, [AttachmentIn("fig1.png", data, "image/png")])
    path, entry = store.get_attachment(doc_id, "fig1.png")
    assert path.read_bytes() == data
    assert entry["content_type"] == "image/png"
    assert entry["sha256"] == hashlib.sha256(data).hexdigest()


def test_add_attachment_conflict_and_overwrite(store: DocumentStore):
    doc_id = make_doc(
        store, attachments=[AttachmentIn("a.txt", b"old", "text/plain")]
    )["id"]
    with pytest.raises(AttachmentAlreadyExists):
        store.add_attachments(doc_id, [AttachmentIn("a.txt", b"new", "text/plain")])
    store.add_attachments(
        doc_id, [AttachmentIn("a.txt", b"new", "text/plain")], overwrite=True
    )
    path, entry = store.get_attachment(doc_id, "a.txt")
    assert path.read_bytes() == b"new"
    assert entry["sha256"] == hashlib.sha256(b"new").hexdigest()
    # Still only one manifest entry for the file.
    manifest = store.read_manifest(doc_id)
    assert [e["filename"] for e in manifest["attachments"]] == ["a.txt"]


def test_get_attachment_traversal_rejected(store: DocumentStore):
    doc_id = make_doc(store)["id"]
    with pytest.raises(InvalidFilename):
        store.get_attachment(doc_id, "../manifest.json")
    with pytest.raises(InvalidFilename):
        store.get_attachment(doc_id, "..\\..\\secrets.txt")


def test_manifest_not_reachable_as_attachment(store: DocumentStore):
    doc_id = make_doc(store)["id"]
    # 'manifest.json' is a safe *name* but lives outside attachments/.
    with pytest.raises(AttachmentNotFound):
        store.get_attachment(doc_id, "manifest.json")


def test_missing_attachment(store: DocumentStore):
    doc_id = make_doc(store)["id"]
    with pytest.raises(AttachmentNotFound):
        store.get_attachment(doc_id, "nope.txt")


# -- delete -------------------------------------------------------------------


def test_delete_document(store: DocumentStore):
    doc_id = make_doc(store)["id"]
    store.delete_document(doc_id)
    assert not (store.root / doc_id).exists()
    with pytest.raises(DocumentNotFound):
        store.read_manifest(doc_id)
    with pytest.raises(DocumentNotFound):
        store.delete_document(doc_id)


# -- search / pagination ------------------------------------------------------


def test_search_by_title_body_tags_and_project(store: DocumentStore):
    a = make_doc(store, metadata={"title": "Quantum widgets", "project_id": "p1"})
    b = make_doc(
        store,
        metadata={"title": "Other", "project_id": "p2", "tags": ["quantum"]},
        body="nothing relevant",
    )
    c = make_doc(
        store,
        metadata={"title": "Third", "project_id": "p1", "tags": []},
        body="deep dive into QUANTUM tunnelling",
    )

    total, items = store.search(query="quantum")
    assert total == 3
    assert {m["id"] for m in items} == {a["id"], b["id"], c["id"]}

    total, items = store.search(query="quantum", project_id="p1")
    assert {m["id"] for m in items} == {a["id"], c["id"]}

    total, items = store.search(tag="quantum")
    assert [m["id"] for m in items] == [b["id"]]

    total, items = store.search(query="no-such-term-anywhere")
    assert total == 0 and items == []


def test_search_filters_by_function(store: DocumentStore):
    research = make_doc(store, metadata={"title": "R", "project_id": "axiom"})
    plan = make_doc(
        store,
        metadata={"title": "P", "project_id": "axiom", "function": "plan"},
    )
    other = make_doc(
        store,
        metadata={"title": "Other plan", "project_id": "bandit", "function": "plan"},
    )

    total, items = store.search(function="plan")
    assert total == 2
    assert {m["id"] for m in items} == {plan["id"], other["id"]}

    total, items = store.search(project_id="axiom", function="plan")
    assert [m["id"] for m in items] == [plan["id"]]

    total, items = store.search(function="research")
    assert [m["id"] for m in items] == [research["id"]]


def test_search_pagination_and_ordering(store: DocumentStore):
    ids = []
    for i in range(5):
        m = make_doc(
            store,
            metadata={
                "title": f"Doc {i}",
                "captured_at": f"2025-01-0{i + 1}T00:00:00+00:00",
            },
        )
        ids.append(m["id"])

    total, page = store.search(limit=2, offset=0)
    assert total == 5
    assert [m["id"] for m in page] == [ids[4], ids[3]]  # newest first

    total, page = store.search(limit=2, offset=2)
    assert [m["id"] for m in page] == [ids[2], ids[1]]

    total, page = store.search(limit=2, offset=4)
    assert [m["id"] for m in page] == [ids[0]]
