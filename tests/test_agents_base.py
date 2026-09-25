from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from student_agent.agents.base import (
    AgentMessage,
    EvidenceStore,
    Specialist,
    send,
)
from student_agent.agents.order_agent import OrderAgent
from student_agent.agents.payment_agent import PaymentAgent
from student_agent.agents.policy_agent import PolicyAgent
from student_agent.agents.shipment_agent import ShipmentAgent
from student_agent.contracts import Contracts
from student_agent.trace import TraceWriter


class FakeGateway:
    """Mock gateway that simulates MCP tool calls conforming to contract."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses = responses or {}
        self.call_history: list[dict[str, Any]] = []
        self.fail_countdown: int = 0
        self.fail_exception: Exception | None = None

    async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict[str, Any]:
        self.call_history.append({"tool_name": tool_name, "case_id": case_id, **arguments})
        if self.fail_countdown > 0:
            self.fail_countdown -= 1
            if self.fail_exception is not None:
                raise self.fail_exception

        key = f"{case_id}:{tool_name}"
        if key in self.responses:
            return self.responses[key]
        if tool_name in self.responses:
            return self.responses[tool_name]

        # Standard simulated MCP response
        ref_id = f"ev_mock_{tool_name}_{case_id.lower()}_12345678901234"
        return {
            "schema_version": "day09-mcp-evidence-v1",
            "evidence_ref": ref_id,
            "result_hash": "sha256:" + "a" * 64,
            "domain": "order" if "order" in tool_name else "payment",
            "data": {"order_id": arguments.get("order_id", "test_order")},
            "warnings": [],
        }


@pytest.fixture
def contracts() -> Contracts:
    root = Path(__file__).resolve().parents[1]
    return Contracts(root / "contracts" / "schemas")


@pytest.fixture
def trace_writer(tmp_path: Path, contracts: Contracts) -> TraceWriter:
    trace_path = tmp_path / "traces" / "trace.jsonl"
    return TraceWriter(trace_path, contracts)


@pytest.mark.asyncio
async def test_evidence_store_isolation_between_cases(
    trace_writer: TraceWriter, contracts: Contracts
) -> None:
    gateway = FakeGateway()

    store_a = EvidenceStore(case_id="CASE_A", gateway=gateway, trace=trace_writer)
    store_b = EvidenceStore(case_id="CASE_B", gateway=gateway, trace=trace_writer)

    rec_a = await store_a.fetch(actor="order-agent", tool_name="get_order", order_id="ord_1")
    assert store_a.owns(rec_a.evidence_ref)
    assert not store_b.owns(rec_a.evidence_ref)
    assert rec_a.evidence_ref in store_a.refs()
    assert rec_a.evidence_ref not in store_b.refs()


@pytest.mark.asyncio
async def test_evidence_store_fetch_emits_tool_result_consumed_trace(
    trace_writer: TraceWriter,
) -> None:
    gateway = FakeGateway()
    store = EvidenceStore(case_id="CASE_001", gateway=gateway, trace=trace_writer)

    rec = await store.fetch(actor="order-agent", tool_name="get_order", order_id="ord_100")
    assert rec.evidence_ref.startswith("ev_")

    # Read trace lines
    trace_lines = trace_writer.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(trace_lines) == 1
    event = json.loads(trace_lines[0])
    assert event["event_type"] == "tool_result_consumed"
    assert event["case_id"] == "CASE_001"
    assert event["actor"] == "order-agent"
    assert event["tool_name"] == "get_order"
    assert event["evidence_refs"] == [rec.evidence_ref]


@pytest.mark.asyncio
async def test_evidence_store_blocks_unauthorized_tools(trace_writer: TraceWriter) -> None:
    gateway = FakeGateway()
    allowed = {"order-agent": {"get_order"}}
    store = EvidenceStore(
        case_id="CASE_001",
        gateway=gateway,
        trace=trace_writer,
        allowed_tools_by_actor=allowed,
    )

    # Authorized tool succeeds
    await store.fetch(actor="order-agent", tool_name="get_order", order_id="ord_1")

    # Unauthorized tool is blocked
    with pytest.raises(PermissionError, match="not authorized"):
        await store.fetch(actor="order-agent", tool_name="get_payment", payment_id="pay_1")


@pytest.mark.asyncio
async def test_evidence_store_retry_behavior_on_network_timeout(
    trace_writer: TraceWriter,
) -> None:
    gateway = FakeGateway()
    gateway.fail_countdown = 2
    gateway.fail_exception = httpx2.TimeoutException("Read timeout")

    store = EvidenceStore(case_id="CASE_001", gateway=gateway, trace=trace_writer)
    rec = await store.fetch(actor="order-agent", tool_name="get_order", order_id="ord_1")

    # Called 3 times (2 retries + 1 success)
    assert len(gateway.call_history) == 3
    assert rec.evidence_ref in store.refs()


@pytest.mark.asyncio
async def test_evidence_store_no_retry_on_not_found(trace_writer: TraceWriter) -> None:
    gateway = FakeGateway()
    gateway.fail_countdown = 2
    gateway.fail_exception = RuntimeError("Order not found in database")

    store = EvidenceStore(case_id="CASE_001", gateway=gateway, trace=trace_writer)
    with pytest.raises(RuntimeError, match="Order not found"):
        await store.fetch(actor="order-agent", tool_name="get_order", order_id="nonexistent")

    # Failed immediately without retrying
    assert len(gateway.call_history) == 1


def test_agent_message_and_send_handoff(trace_writer: TraceWriter) -> None:
    msg = AgentMessage(
        case_id="CASE_001",
        sender="order-agent",
        recipient="coordinator",
        task="order_investigation_completed",
        payload={"order_status": "canceled"},
    )
    send(msg, trace_writer)

    trace_lines = trace_writer.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(trace_lines) == 1
    event = json.loads(trace_lines[0])
    assert event["event_type"] == "handoff"
    assert event["actor"] == "order-agent"
    assert event["target"] == "coordinator"
    assert event["decision_code"] == "order_investigation_completed"


def test_specialists_conform_to_protocol() -> None:
    assert isinstance(OrderAgent(), Specialist)
    assert isinstance(ShipmentAgent(), Specialist)
    assert isinstance(PaymentAgent(), Specialist)
    assert isinstance(PolicyAgent(), Specialist)
