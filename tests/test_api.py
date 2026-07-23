"""API tests for backend.main (FastAPI over the vault)."""

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import Settings, create_app
from tests.conftest import WRITE_TOKEN, create_doc


# -- health -------------------------------------------------------------------


def test_health(client: TestClient, vault_root: Path):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "mneme"
    assert data["documents"] == 0
    assert data["writes_enabled"] is True
    assert Path(data["vault_root"]) == vault_root.resolve()


def test_api_prefix_is_canonical(client: TestClient):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/documents").status_code == 200


def test_operator_ui_is_served(client: TestClient):
    page = client.get("/")
    assert page.status_code == 200
    assert "Mneme" in page.text
    assert client.get("/static/app.js").status_code == 200


# -- auth ---------------------------------------------------------------------


class TestAuth:
    def test_writes_fail_closed_without_configured_token(self, vault_root):
        app = create_app(
            Settings(vault_root=vault_root, write_token=None, max_upload_bytes=1024)
        )
        client = TestClient(app)
        resp = client.post(
            "/documents",
            data={"metadata": "{}"},
            headers={"Authorization": "Bearer anything"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "mneme_read_only"
        # Reads stay open.
        assert client.get("/documents").status_code == 200
        assert client.get("/health").json()["writes_enabled"] is False

    def test_missing_token_rejected(self, client):
        resp = client.post("/documents", data={"metadata": "{}"})
        assert resp.status_code == 403

    def test_wrong_token_rejected(self, client):
        resp = client.post(
            "/documents",
            data={"metadata": "{}"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 403

    def test_wrong_scheme_rejected(self, client):
        resp = client.delete(
            f"/documents/{'a' * 32}",
            headers={"Authorization": f"Basic {WRITE_TOKEN}"},
        )
        assert resp.status_code == 403

    @pytest.mark.parametrize(
        "method,path",
        [
            ("POST", "/documents"),
            ("PATCH", "/documents/" + "a" * 32),
            ("POST", "/documents/" + "a" * 32 + "/attachments"),
            ("DELETE", "/documents/" + "a" * 32),
        ],
    )
    def test_all_write_endpoints_gated(self, client, method, path):
        assert client.request(method, path).status_code == 403

    def test_reads_are_open(self, client, auth):
        doc_id = create_doc(client, auth).json()["id"]
        assert client.get("/documents").status_code == 200
        assert client.get(f"/documents/{doc_id}").status_code == 200


# -- create -------------------------------------------------------------------


def test_create_document_full(client: TestClient, auth):
    pdf = b"%PDF-1.4 fake"
    png = b"\x89PNG fake"
    resp = create_doc(
        client,
        auth,
        project_id="research-x",
        title="Attention Is All You Need",
        author="Vaswani et al.",
        publisher="NeurIPS",
        published_at="2017-06-12T00:00:00Z",
        source_url="https://arxiv.org/abs/1706.03762",
        captured_at="2025-05-01T10:00:00Z",
        tags=["transformers", "nlp"],
        body="# Notes\n\ntransformer architecture",
        attachments=[
            ("paper.pdf", pdf, "application/pdf"),
            ("fig1.png", png, "image/png"),
        ],
    )
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    assert doc["project_id"] == "research-x"
    assert doc["function"] == "research"
    assert doc["title"] == "Attention Is All You Need"
    assert doc["author"] == "Vaswani et al."
    assert doc["publisher"] == "NeurIPS"
    assert doc["source_url"] == "https://arxiv.org/abs/1706.03762"
    assert doc["tags"] == ["transformers", "nlp"]
    assert doc["body"] == "# Notes\n\ntransformer architecture"
    assert doc["created_at"] and doc["updated_at"] and doc["captured_at"]

    by_name = {a["filename"]: a for a in doc["attachments"]}
    assert by_name["paper.pdf"]["content_type"] == "application/pdf"
    assert by_name["paper.pdf"]["sha256"] == hashlib.sha256(pdf).hexdigest()
    assert by_name["fig1.png"]["content_type"] == "image/png"
    assert by_name["fig1.png"]["size"] == len(png)


def test_create_minimal_document(client: TestClient, auth):
    resp = create_doc(client, auth, body="")
    assert resp.status_code == 201
    doc = resp.json()
    assert doc["body"] == ""
    assert doc["attachments"] == []
    assert doc["captured_at"]  # defaulted server-side
    assert doc["function"] == "research"


def test_create_plan_without_source_url(client: TestClient, auth):
    resp = create_doc(
        client,
        auth,
        project_id="axiom",
        function="plan",
        title="Authored plan",
        author="Control Alt",
        publisher="Control Alt",
        source_url=None,
        tags=["integration-plan"],
        body="# Plan\n\nForward-looking design.",
    )
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    assert doc["function"] == "plan"
    assert doc["source_url"] is None
    assert doc["project_id"] == "axiom"
    assert doc["tags"] == ["integration-plan"]


@pytest.mark.parametrize(
    "metadata",
    [
        "not json",
        json.dumps({"title": "no project"}),
        json.dumps({"project_id": "p", "title": ""}),
        json.dumps({"project_id": "../evil", "title": "t"}),
        json.dumps({"project_id": "p", "title": "t", "source_url": "ftp://x"}),
        json.dumps({"project_id": "p", "title": "t", "unknown_field": 1}),
        json.dumps({"project_id": "p", "title": "t", "function": "doctrine"}),
    ],
)
def test_create_rejects_invalid_metadata(client: TestClient, auth, metadata):
    resp = client.post("/documents", data={"metadata": metadata}, headers=auth)
    assert resp.status_code == 422


def test_create_rejects_unsafe_attachment_filename(client: TestClient, auth):
    resp = create_doc(
        client, auth, attachments=[("../evil.txt", b"x", "text/plain")]
    )
    assert resp.status_code == 400
    assert "filename" in resp.json()["detail"].lower()


# -- get ----------------------------------------------------------------------


def test_get_document_with_body(client: TestClient, auth):
    doc_id = create_doc(client, auth, body="# Body\n\ncontent").json()["id"]
    resp = client.get(f"/documents/{doc_id}")
    assert resp.status_code == 200
    assert resp.json()["body"] == "# Body\n\ncontent"


def test_get_unknown_document_404(client: TestClient):
    assert client.get(f"/documents/{'d' * 32}").status_code == 404


def test_get_malformed_doc_id_400(client: TestClient):
    resp = client.get("/documents/not-a-valid-id")
    assert resp.status_code == 400
    assert "invalid document id" in resp.json()["detail"]


def test_get_document_etag(client: TestClient, auth):
    doc_id = create_doc(client, auth, body="content").json()["id"]
    resp = client.get(f"/documents/{doc_id}")
    assert resp.status_code == 200
    etag = resp.headers.get("etag")
    assert etag is not None
    assert etag.startswith('"') and etag.endswith('"')

    resp2 = client.get(f"/documents/{doc_id}", headers={"If-None-Match": etag})
    assert resp2.status_code == 304

    resp3 = client.get(f"/documents/{doc_id}", headers={"If-None-Match": '"wrong"'})
    assert resp3.status_code == 200

    client.patch(f"/documents/{doc_id}", json={"title": "Updated"}, headers=auth)
    resp4 = client.get(f"/documents/{doc_id}", headers={"If-None-Match": etag})
    assert resp4.status_code == 200
    assert resp4.headers.get("etag") != etag


# -- attachments --------------------------------------------------------------


def test_fetch_attachment_content_and_headers(client: TestClient, auth):
    data = b"col_a,col_b\n1,2\n"
    doc_id = create_doc(
        client, auth, attachments=[("data.csv", data, "text/csv")]
    ).json()["id"]
    resp = client.get(f"/documents/{doc_id}/attachments/data.csv")
    assert resp.status_code == 200
    assert resp.content == data
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["etag"] == f'"{hashlib.sha256(data).hexdigest()}"'


def test_fetch_attachment_corrupted_returns_500(client: TestClient, auth, vault_root):
    data = b"original bytes"
    doc_id = create_doc(
        client, auth, attachments=[("file.txt", data, "text/plain")]
    ).json()["id"]
    att_path = vault_root / doc_id / "attachments" / "file.txt"
    att_path.write_bytes(b"corrupted")
    resp = client.get(f"/documents/{doc_id}/attachments/file.txt")
    assert resp.status_code == 500
    assert "checksum mismatch" in resp.json()["detail"]


def test_fetch_attachment_traversal_blocked(client: TestClient, auth):
    doc_id = create_doc(client, auth).json()["id"]
    # Depending on the router version this is rejected at routing time (404)
    # or by the filename sanitizer (400); either way traversal is denied.
    resp = client.get(f"/documents/{doc_id}/attachments/..%2Fmanifest.json")
    assert resp.status_code in (400, 404)
    # A safe-looking name that exists outside attachments/ is still unreachable.
    resp = client.get(f"/documents/{doc_id}/attachments/manifest.json")
    assert resp.status_code == 404
    resp = client.get(f"/documents/{doc_id}/attachments/body.md")
    assert resp.status_code == 404


def test_add_attachments_endpoint(client: TestClient, auth):
    doc_id = create_doc(client, auth).json()["id"]
    resp = client.post(
        f"/documents/{doc_id}/attachments",
        files=[("attachments", ("extra.txt", b"extra", "text/plain"))],
        headers=auth,
    )
    assert resp.status_code == 201
    assert [a["filename"] for a in resp.json()["attachments"]] == ["extra.txt"]

    # Duplicate without overwrite conflicts.
    resp = client.post(
        f"/documents/{doc_id}/attachments",
        files=[("attachments", ("extra.txt", b"v2", "text/plain"))],
        headers=auth,
    )
    assert resp.status_code == 409

    # Overwrite succeeds and updates the checksum.
    resp = client.post(
        f"/documents/{doc_id}/attachments?overwrite=true",
        files=[("attachments", ("extra.txt", b"v2", "text/plain"))],
        headers=auth,
    )
    assert resp.status_code == 201
    (entry,) = resp.json()["attachments"]
    assert entry["sha256"] == hashlib.sha256(b"v2").hexdigest()
    fetched = client.get(f"/documents/{doc_id}/attachments/extra.txt")
    assert fetched.content == b"v2"


# -- patch --------------------------------------------------------------------


def test_patch_metadata_and_body(client: TestClient, auth):
    doc_id = create_doc(client, auth, tags=["old"]).json()["id"]
    resp = client.patch(
        f"/documents/{doc_id}",
        json={
            "title": "Renamed",
            "function": "plan",
            "tags": ["new", "new", " x "],
            "body": "updated",
        },
        headers=auth,
    )
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["title"] == "Renamed"
    assert doc["function"] == "plan"
    assert doc["tags"] == ["new", "x"]  # deduped and stripped
    assert doc["body"] == "updated"
    assert doc["project_id"] == "proj-a"  # untouched


def test_patch_preserves_function_when_omitted(client: TestClient, auth):
    doc_id = create_doc(client, auth, function="plan").json()["id"]
    resp = client.patch(
        f"/documents/{doc_id}", json={"title": "Still a plan"}, headers=auth
    )
    assert resp.status_code == 200
    assert resp.json()["function"] == "plan"
    assert resp.json()["title"] == "Still a plan"


def test_patch_body_only(client: TestClient, auth):
    doc_id = create_doc(client, auth, title="Keep Me").json()["id"]
    resp = client.patch(
        f"/documents/{doc_id}", json={"body": "just body"}, headers=auth
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Keep Me"
    assert resp.json()["body"] == "just body"


def test_patch_requires_fields(client: TestClient, auth):
    doc_id = create_doc(client, auth).json()["id"]
    assert client.patch(f"/documents/{doc_id}", json={}, headers=auth).status_code == 422


def test_patch_rejects_unknown_fields(client: TestClient, auth):
    doc_id = create_doc(client, auth).json()["id"]
    resp = client.patch(
        f"/documents/{doc_id}", json={"bogus": True}, headers=auth
    )
    assert resp.status_code == 422


def test_patch_unknown_document_404(client: TestClient, auth):
    resp = client.patch(
        f"/documents/{'e' * 32}", json={"title": "x"}, headers=auth
    )
    assert resp.status_code == 404


# -- delete -------------------------------------------------------------------


def test_delete_document(client: TestClient, auth):
    doc_id = create_doc(client, auth).json()["id"]
    assert client.delete(f"/documents/{doc_id}", headers=auth).status_code == 204
    assert client.get(f"/documents/{doc_id}").status_code == 404
    assert client.delete(f"/documents/{doc_id}", headers=auth).status_code == 404


# -- list / search ------------------------------------------------------------


def test_list_search_and_pagination(client: TestClient, auth):
    ids = []
    for i in range(3):
        ids.append(
            create_doc(
                client,
                auth,
                project_id="p1" if i < 2 else "p2",
                title=f"Alpha {i}" if i != 1 else "Beta special",
                tags=["shared"] if i == 0 else [],
                body=f"body {i}",
                captured_at=f"2025-03-0{i + 1}T00:00:00Z",
            ).json()["id"]
        )

    resp = client.get("/documents")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert [d["id"] for d in data["items"]] == list(reversed(ids))  # newest first
    assert "body" not in data["items"][0]  # summaries omit the body
    assert data["items"][0]["function"] == "research"

    assert client.get("/documents", params={"q": "beta"}).json()["total"] == 1
    assert client.get("/documents", params={"q": "body 2"}).json()["total"] == 1
    assert client.get("/documents", params={"project_id": "p1"}).json()["total"] == 2
    assert client.get("/documents", params={"tag": "shared"}).json()["total"] == 1
    combo = client.get(
        "/documents", params={"q": "alpha", "project_id": "p2"}
    ).json()
    assert [d["id"] for d in combo["items"]] == [ids[2]]

    page = client.get("/documents", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 3
    assert [d["id"] for d in page["items"]] == [ids[1]]

    assert client.get("/documents", params={"limit": 0}).status_code == 422
    assert client.get("/documents", params={"offset": -1}).status_code == 422


def test_list_filter_by_function(client: TestClient, auth, vault_root: Path):
    research_id = create_doc(
        client, auth, project_id="axiom", title="Research note"
    ).json()["id"]
    plan_id = create_doc(
        client,
        auth,
        project_id="axiom",
        function="plan",
        title="Cue plan",
        source_url=None,
    ).json()["id"]
    other_plan = create_doc(
        client,
        auth,
        project_id="bandit",
        function="plan",
        title="Other plan",
    ).json()["id"]

    # Legacy manifest without function behaves as research.
    from backend.store import DocumentStore, MANIFEST_NAME, new_doc_id

    legacy_id = new_doc_id()
    legacy_dir = vault_root / legacy_id
    legacy_dir.mkdir()
    (legacy_dir / "body.md").write_text("legacy research", encoding="utf-8")
    (legacy_dir / MANIFEST_NAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": legacy_id,
                "project_id": "axiom",
                "title": "Legacy doc",
                "created_at": "2025-01-01T00:00:00Z",
                "updated_at": "2025-01-01T00:00:00Z",
                "captured_at": "2025-01-01T00:00:00Z",
                "tags": [],
                "attachments": [],
            }
        ),
        encoding="utf-8",
    )
    assert DocumentStore(vault_root).read_manifest(legacy_id).get("function") is None

    plans = client.get("/documents", params={"function": "plan"}).json()
    assert {d["id"] for d in plans["items"]} == {plan_id, other_plan}
    assert all(d["function"] == "plan" for d in plans["items"])

    research = client.get("/documents", params={"function": "research"}).json()
    assert {d["id"] for d in research["items"]} == {research_id, legacy_id}
    assert all(d["function"] == "research" for d in research["items"])

    scoped = client.get(
        "/documents", params={"project_id": "axiom", "function": "plan"}
    ).json()
    assert [d["id"] for d in scoped["items"]] == [plan_id]

    assert client.get("/documents", params={"function": "doctrine"}).status_code == 422


def test_operator_ui_exposes_function_controls(client: TestClient):
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="function-filter"' in page.text
    assert 'name="function"' in page.text
    assert "value=\"plan\"" in page.text
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert 'params.set("function"' in js.text
    assert "function:" in js.text


# -- upload limits ------------------------------------------------------------


@pytest.fixture
def tiny_client(vault_root):
    return TestClient(
        create_app(
            Settings(
                vault_root=vault_root, write_token=WRITE_TOKEN, max_upload_bytes=64
            )
        )
    )


def test_create_body_too_large(tiny_client, auth):
    resp = create_doc(tiny_client, auth, body="x" * 100)
    assert resp.status_code == 413


def test_create_attachments_over_budget(tiny_client, auth):
    resp = create_doc(
        tiny_client,
        auth,
        body="tiny",
        attachments=[
            ("a.bin", b"a" * 40, "application/octet-stream"),
            ("b.bin", b"b" * 40, "application/octet-stream"),
        ],
    )
    assert resp.status_code == 413


def test_add_attachment_too_large(tiny_client, auth):
    doc_id = create_doc(tiny_client, auth, body="ok").json()["id"]
    resp = tiny_client.post(
        f"/documents/{doc_id}/attachments",
        files=[("attachments", ("big.bin", b"z" * 100, "application/octet-stream"))],
        headers=auth,
    )
    assert resp.status_code == 413


def test_patch_body_too_large(tiny_client, auth):
    doc_id = create_doc(tiny_client, auth, body="ok").json()["id"]
    resp = tiny_client.patch(
        f"/documents/{doc_id}", json={"body": "y" * 100}, headers=auth
    )
    assert resp.status_code == 413


def test_within_limit_succeeds(tiny_client, auth):
    resp = create_doc(
        tiny_client,
        auth,
        body="small",
        attachments=[("ok.txt", b"fits", "text/plain")],
    )
    assert resp.status_code == 201
