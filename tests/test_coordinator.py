from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from student_agent.contracts import Contracts
from student_agent.trace import TraceWriter
from student_agent.workflow import solve_case


class FakeGateway:
    async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict[str, Any]:
        return {
            "schema_version": "day09-mcp-evidence-v1",
            "evidence_ref": f"ev_coord_{tool_name}_{case_id.lower()}_12345678901234",
            "result_hash": "sha256:" + "c" * 64,
            "domain": "order",
            "data": {"order_id": "test_order"},
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
async def test_solve_case_end_to_end_conforms_to_contract_and_trace(
    trace_writer: TraceWriter,
    contracts: Contracts,
) -> None:
    case = {
        "case_id": "L3A_CASE_001",
        "opened_at": "2018-01-01T09:00:00-03:00",
        "customer_request": {
            "language": "vi",
            "message": "Đơn hàng bị huỷ nhưng tài khoản đã bị trừ tiền.",
            "claimed_order_id": "e2a03ccf5ea816036608b2d8c3ab8e60",
            "claims": [
                {"claim_id": "claim-001-a", "topic": "canceled_order_paid"},
                {"claim_id": "claim-001-b", "topic": "requested_full_refund"},
            ],
        },
        "policy_version": "EC_POLICY_V1",
    }

    gateway = FakeGateway()

    # Simulate CLI emitting case_received before solve_case
    trace_writer.emit(case_id=case["case_id"], event_type="case_received", actor="coordinator")

    output = await solve_case(case, gateway, trace_writer)

    # Simulate CLI emitting case_finalized after solve_case
    trace_writer.emit(case_id=case["case_id"], event_type="case_finalized", actor="coordinator")

    # 1. Output must pass JSON Schema
    contracts.validate_output(output, "coordinator output")
    assert output["case_id"] == "L3A_CASE_001"
    assert "assessment" in output
    assert "root_cause_analysis" in output
    assert "financial_resolution" in output

    # 2. Trace must pass schema and contain required lifecycle events in order
    trace_lines = trace_writer.path.read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line) for line in trace_lines]

    event_types = [e["event_type"] for e in events]
    actors = {e["actor"] for e in events}

    # Verify minimum required events
    assert "case_received" in event_types
    assert "task_assigned" in event_types
    assert "handoff" in event_types
    assert "policy_decided" in event_types
    assert "verification_completed" in event_types
    assert "case_finalized" in event_types

    # Ordering check: case_received first, case_finalized last
    assert event_types[0] == "case_received"
    assert event_types[-1] == "case_finalized"

    # Multi-agent collaboration: at least 3 distinct actors
    assert len(actors) >= 3
