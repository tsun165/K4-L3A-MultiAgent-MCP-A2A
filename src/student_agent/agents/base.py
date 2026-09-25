from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx2

from ..trace import TraceWriter


@dataclass(frozen=True)
class EvidenceRecord:
    """Immutable record of an authoritative MCP evidence item."""

    evidence_ref: str
    result_hash: str
    domain: str
    tool_name: str
    data: Any
    warnings: list[str] = field(default_factory=list)


class EvidenceStore:
    """Per-case evidence storage, authorization and audit manager.

    A completely new instance must be created for every single case.
    """

    def __init__(
        self,
        case_id: str,
        gateway: Any,
        trace: TraceWriter,
        allowed_tools_by_actor: dict[str, set[str]] | None = None,
    ) -> None:
        self.case_id = case_id
        self.gateway = gateway
        self.trace = trace
        self._allowed_tools: dict[str, set[str]] = (
            {k: set(v) for k, v in allowed_tools_by_actor.items()}
            if allowed_tools_by_actor is not None
            else {}
        )
        self._records: dict[str, EvidenceRecord] = {}

    def register_actor_tools(self, actor: str, tools: set[str]) -> None:
        """Register allowed tools for a specific actor."""
        self._allowed_tools[actor] = set(tools)

    def check_permission(self, actor: str, tool_name: str) -> None:
        """Verify if actor has authorization to invoke tool_name.

        Fail-closed: an actor that was never registered has no tools at all.
        """
        if tool_name not in self._allowed_tools.get(actor, frozenset()):
            raise PermissionError(
                f"Actor '{actor}' is not authorized to call tool '{tool_name}'."
            )

    async def fetch(
        self,
        actor: str,
        tool_name: str,
        **arguments: Any,
    ) -> EvidenceRecord:
        """Fetch evidence through MCP gateway with authorization check and bounded retries.

        Retries up to 2 times with exponential backoff on network/timeout errors.
        Does NOT retry on not-found or contract validation errors.
        """
        self.check_permission(actor, tool_name)

        max_retries = 2
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                # Gateway call validates contract and returns raw MCP response
                evidence = await self.gateway.call(tool_name, case_id=self.case_id, **arguments)
                record = EvidenceRecord(
                    evidence_ref=evidence["evidence_ref"],
                    result_hash=evidence["result_hash"],
                    domain=evidence["domain"],
                    tool_name=tool_name,
                    data=evidence.get("data"),
                    warnings=list(evidence.get("warnings", [])),
                )
                self._records[record.evidence_ref] = record

                # Emit audit trace for evidence consumption
                self.trace.emit(
                    case_id=self.case_id,
                    event_type="tool_result_consumed",
                    actor=actor,
                    tool_name=tool_name,
                    evidence_refs=[record.evidence_ref],
                )
                return record
            except (httpx2.TimeoutException, httpx2.NetworkError, TimeoutError) as exc:
                last_error = exc
                if attempt < max_retries:
                    await asyncio.sleep(0.05 * (2**attempt))
                    continue
                raise
            except Exception as exc:
                # Do not retry on not-found or application/business errors
                err_msg = str(exc).lower()
                if "not found" in err_msg or "not_found" in err_msg:
                    raise
                # Check for generic connection/timeout indications
                if any(k in err_msg for k in ["timeout", "timed out", "connection reset"]):
                    last_error = exc
                    if attempt < max_retries:
                        await asyncio.sleep(0.05 * (2**attempt))
                        continue
                raise

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Failed to fetch evidence via {tool_name} after retries")

    def owns(self, ref: str) -> bool:
        """Check whether ref was acquired in the context of this case."""
        return ref in self._records

    def records(self, domain: str | None = None) -> list[EvidenceRecord]:
        """Return collected evidence records, optionally filtered by domain."""
        if domain is None:
            return list(self._records.values())
        return [rec for rec in self._records.values() if rec.domain == domain]

    def refs(self) -> set[str]:
        """Return the set of all evidence refs collected in this case."""
        return set(self._records.keys())


@dataclass
class AgentMessage:
    """Agent-to-agent communication envelope."""

    case_id: str
    sender: str
    recipient: str
    task: str
    payload: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    message_id: str = field(default_factory=lambda: f"msg_{secrets.token_urlsafe(12)}")


def send(message: AgentMessage, trace: TraceWriter) -> None:
    """Deliver an agent message and emit observable handoff trace event."""
    trace.emit(
        case_id=message.case_id,
        event_type="handoff",
        actor=message.sender,
        target=message.recipient,
        decision_code=message.task,
        evidence_refs=message.evidence_refs if message.evidence_refs else None,
    )


@dataclass
class SpecialistResult:
    """Standardized output structure returned by all specialist agents."""

    actor: str
    findings: dict[str, Any] = field(default_factory=dict)
    candidate_issues: list[tuple[str, float]] = field(default_factory=list)
    entities: dict[str, list[str]] = field(
        default_factory=lambda: {
            "order_ids": [],
            "item_ids": [],
            "seller_ids": [],
            "payment_references": [],
            "shipment_ids": [],
        }
    )
    cause_codes: list[str] = field(default_factory=list)
    responsible_parties: list[dict[str, Any]] = field(default_factory=list)
    refund_lines: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@runtime_checkable
class Specialist(Protocol):
    """Protocol for specialized domain agents."""

    name: str
    allowed_tools: set[str]

    async def run(
        self, case: dict[str, Any], store: EvidenceStore, trace: TraceWriter
    ) -> SpecialistResult:
        ...
