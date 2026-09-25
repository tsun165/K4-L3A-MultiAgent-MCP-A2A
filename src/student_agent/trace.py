from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import Contracts


class CaseTraceBuffer:
    """Per-case in-memory trace buffer to ensure contiguous event blocks during concurrency."""

    def __init__(self, contracts: Contracts) -> None:
        self.contracts = contracts
        self.events: list[dict[str, Any]] = []

    def emit(
        self,
        *,
        case_id: str,
        event_type: str,
        actor: str,
        target: str | None = None,
        decision_code: str | None = None,
        tool_name: str | None = None,
        evidence_refs: list[str] | None = None,
        attributes: dict[str, str | int | float | bool | None] | None = None,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "schema_version": "day09-trace-event-v1",
            "event_id": f"evt_{secrets.token_urlsafe(18)}",
            "case_id": case_id,
            "event_type": event_type,
            "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "actor": actor,
        }
        optional = {
            "target": target,
            "decision_code": decision_code,
            "tool_name": tool_name,
            "evidence_refs": evidence_refs,
            "attributes": attributes,
        }
        event.update({key: value for key, value in optional.items() if value is not None})
        self.contracts.validate_trace(event, "trace event")
        self.events.append(event)
        return event


class TraceWriter:
    """Append observable workflow events. Never put prompts or chain-of-thought here."""

    def __init__(self, path: Path, contracts: Contracts) -> None:
        self.path = path
        self.contracts = contracts
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def create_case_buffer(self) -> CaseTraceBuffer:
        """Create an isolated in-memory buffer for a single case."""
        return CaseTraceBuffer(self.contracts)

    def append_events(self, events: list[dict[str, Any]]) -> None:
        """Append a contiguous block of verified events for a completed case."""
        if not events:
            return
        lines = [json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n" for e in events]
        with self.path.open("a", encoding="utf-8") as handle:
            handle.writelines(lines)

    def emit(
        self,
        *,
        case_id: str,
        event_type: str,
        actor: str,
        target: str | None = None,
        decision_code: str | None = None,
        tool_name: str | None = None,
        evidence_refs: list[str] | None = None,
        attributes: dict[str, str | int | float | bool | None] | None = None,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "schema_version": "day09-trace-event-v1",
            "event_id": f"evt_{secrets.token_urlsafe(18)}",
            "case_id": case_id,
            "event_type": event_type,
            "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "actor": actor,
        }
        optional = {
            "target": target,
            "decision_code": decision_code,
            "tool_name": tool_name,
            "evidence_refs": evidence_refs,
            "attributes": attributes,
        }
        event.update({key: value for key, value in optional.items() if value is not None})
        self.contracts.validate_trace(event, "trace event")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        return event
