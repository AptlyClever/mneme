"""Tests for backend.audit (append-only JSONL audit log)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.audit import AuditLogger
from backend.main import Settings, create_app
from tests.conftest import WRITE_TOKEN, create_doc


@pytest.fixture
def logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(tmp_path / "vault")


def test_log_creates_file(logger: AuditLogger):
    logger.log("create", doc_id="abc123", ip="127.0.0.1", user_agent="test")
    assert logger._path.is_file()
    total, entries = logger.read_entries()
    assert total == 1
    assert entries[0]["action"] == "create"
    assert entries[0]["doc_id"] == "abc123"
    assert entries[0]["ip"] == "127.0.0.1"
    assert entries[0]["user_agent"] == "test"
    assert entries[0]["ts"]


def test_log_appends(logger: AuditLogger):
    logger.log("create", doc_id="a")
    logger.log("update", doc_id="a")
    logger.log("delete", doc_id="a")
    total, entries = logger.read_entries()
    assert total == 3
    assert [e["action"] for e in entries] == ["create", "update", "delete"]


def test_read_entries_pagination(logger: AuditLogger):
    for i in range(5):
        logger.log("create", doc_id=f"doc-{i}")
    total, entries = logger.read_entries(limit=2, offset=0)
    assert total == 5
    assert len(entries) == 2
    total, entries = logger.read_entries(limit=2, offset=3)
    assert total == 5
    assert len(entries) == 2


def test_read_entries_empty(logger: AuditLogger):
    total, entries = logger.read_entries()
    assert total == 0
    assert entries == []


def test_log_optional_fields(logger: AuditLogger):
    logger.log("create")
    total, entries = logger.read_entries()
    assert total == 1
    assert entries[0]["action"] == "create"
    assert "doc_id" not in entries[0]
    assert "ip" not in entries[0]
    assert "user_agent" not in entries[0]


def test_audit_endpoint_gated(vault_root):
    app = create_app(
        Settings(vault_root=vault_root, write_token=None, max_upload_bytes=1024)
    )
    client = TestClient(app)
    resp = client.get("/api/audit")
    assert resp.status_code == 403


def test_audit_endpoint_returns_entries(client: TestClient, auth):
    create_doc(client, auth, title="Test doc")
    resp = client.get("/api/audit", headers=auth)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert data["items"][0]["action"] == "create"


def test_audit_logs_all_write_operations(client: TestClient, auth):
    doc_id = create_doc(client, auth, title="Test").json()["id"]
    client.patch(f"/documents/{doc_id}", json={"title": "Updated"}, headers=auth)
    client.post(
        f"/documents/{doc_id}/attachments",
        files=[("attachments", ("a.txt", b"data", "text/plain"))],
        headers=auth,
    )
    client.delete(f"/documents/{doc_id}", headers=auth)

    resp = client.get("/api/audit", headers=auth)
    actions = [e["action"] for e in resp.json()["items"]]
    assert "create" in actions
    assert "update" in actions
    assert "attach" in actions
    assert "delete" in actions