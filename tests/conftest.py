import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import Settings, create_app

WRITE_TOKEN = "test-write-token"


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    return tmp_path / "vault"


@pytest.fixture
def settings(vault_root: Path) -> Settings:
    return Settings(
        vault_root=vault_root,
        write_token=WRITE_TOKEN,
        max_upload_bytes=1024 * 1024,
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


@pytest.fixture
def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {WRITE_TOKEN}"}


def create_doc(
    client: TestClient,
    auth: dict[str, str],
    *,
    project_id: str = "proj-a",
    title: str = "Sample Doc",
    function: str = "research",
    body: str = "# Hello\n\nSome research notes.",
    tags: list[str] | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
    **extra_metadata,
):
    metadata = {
        "project_id": project_id,
        "title": title,
        "function": function,
        "provenance": {"source_type": "human_capture"},
        "tags": tags or [],
        **extra_metadata,
    }
    files = [
        ("attachments", (name, content, content_type))
        for name, content, content_type in (attachments or [])
    ]
    return client.post(
        "/documents",
        data={"metadata": json.dumps(metadata), "body": body},
        files=files or None,
        headers=auth,
    )
