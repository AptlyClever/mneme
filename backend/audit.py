"""Append-only audit log for Mneme write operations.

Each entry is a single JSON line written to ``<vault_root>/.audit.jsonl``.
The file is append-only; entries are never modified or removed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

AUDIT_FILENAME = ".audit.jsonl"


class AuditLogger:
    """Append-only JSONL audit log for write operations."""

    def __init__(self, vault_root: Path) -> None:
        self._path = vault_root / AUDIT_FILENAME

    def log(
        self,
        action: str,
        doc_id: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
        }
        if doc_id is not None:
            entry["doc_id"] = doc_id
        if ip is not None:
            entry["ip"] = ip
        if user_agent is not None:
            entry["user_agent"] = user_agent
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def read_entries(
        self, limit: int = 50, offset: int = 0
    ) -> tuple[int, list[dict[str, Any]]]:
        if not self._path.is_file():
            return 0, []
        entries: list[dict[str, Any]] = []
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        total = len(entries)
        return total, entries[offset : offset + limit]