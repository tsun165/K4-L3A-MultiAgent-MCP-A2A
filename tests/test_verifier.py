from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from student_agent.agents.base import EvidenceRecord, EvidenceStore
from student_agent.agents.verifier import Verifier
from student_agent.contracts import Contracts
from student_agent.trace import TraceWriter


class FakeGateway:
    async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict[str, Any]:
        return {
            "schema_version": "day09-mcp-evidence-v1",
            "evidence_ref": f"ev_mock_{tool_name}_{case_id.lower()}_12345678901234",
            "result_hash": "sha256:" + "b" * 64,
            "domain": "order",
            "data": {},
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


@pytest.fixture
def base_valid_output() -> dict[str, Any]:
    return {
        "schema_version": "day09-l3a-output-v2",
        "case_id": "L3A_CASE_001",
        "assessment": {
            "primary_issue": "canceled_order_paid",
            "case_status": "action_required",
            "confidence": 0.90,
        },
        "affected_entities": {
            "order_ids": ["ord_1"],
            "item_ids": ["item_1"],
            "seller_ids": ["seller_1"],
            "payment_references": ["pay_1"],
            "shipment_ids": ["ship_1"],
        },
        "claim_assessments": [
            {
                "claim_id": "claim-1",
                "verdict": "supported",
                "confidence": 0.90,
                "evidence_refs": [],
            }
        ],
        "root_cause_analysis": {
            "ranked_causes": [{"cause_code": "ORDER_CANCELED_PAID", "rank": 1}],
            "responsible_parties": [{"party_type": "seller", "party_id": "seller_1"}],
        },
        "evidence_refs": [],
        "data_conflicts": [],
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": 150.00,
            "refund_lines": [
                {"reason_code": "ORDER_CANCELED", "amount_brl": 150.00, "entity_id": "ord_1"}
            ],
        },
        "resolution_actions": ["issue_refund"],
    }


def test_verifier_passes_clean_output(
    base_valid_output: dict[str, Any],
    trace_writer: TraceWriter,
    contracts: Contracts,
) -> None:
    store = EvidenceStore("L3A_CASE_001", FakeGateway(), trace_writer)
    verifier = Verifier(contracts)

    result = verifier.verify_and_repair(base_valid_output, store, trace_writer)
    assert result["case_id"] == "L3A_CASE_001"

    trace_lines = trace_writer.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(trace_lines) == 1
    event = json.loads(trace_lines[0])
    assert event["event_type"] == "verification_completed"
    assert event["decision_code"] == "PASS"


def test_verifier_filters_foreign_evidence_refs(
    base_valid_output: dict[str, Any],
    trace_writer: TraceWriter,
    contracts: Contracts,
) -> None:
    store = EvidenceStore("L3A_CASE_001", FakeGateway(), trace_writer)
    # Add a valid ref to store
    valid_ref = "ev_valid_store_ref_0123456789012345"
    store._records[valid_ref] = EvidenceRecord(
        evidence_ref=valid_ref,
        result_hash="sha256:" + "0" * 64,
        domain="order",
        tool_name="get_order",
        data={},
    )

    foreign_ref = "ev_foreign_attacker_ref_9999999999999"
    base_valid_output["evidence_refs"] = [valid_ref, foreign_ref]
    base_valid_output["claim_assessments"][0]["evidence_refs"] = [foreign_ref]

    verifier = Verifier(contracts)
    result = verifier.verify_and_repair(base_valid_output, store, trace_writer)

    # Foreign ref must be removed
    assert result["evidence_refs"] == [valid_ref]
    assert result["claim_assessments"][0]["evidence_refs"] == []

    trace_lines = trace_writer.path.read_text(encoding="utf-8").strip().splitlines()
    event = json.loads(trace_lines[-1])
    assert event["decision_code"] == "REPAIRED"


def test_verifier_repairs_mismatched_refund_totals(
    base_valid_output: dict[str, Any],
    trace_writer: TraceWriter,
    contracts: Contracts,
) -> None:
    store = EvidenceStore("L3A_CASE_001", FakeGateway(), trace_writer)
    # Recommended refund is 200, but line is 150.00
    base_valid_output["financial_resolution"]["recommended_refund_brl"] = 200.00
    base_valid_output["financial_resolution"]["refund_lines"] = [
        {"reason_code": "TEST", "amount_brl": 150.00, "entity_id": "item_1"}
    ]

    verifier = Verifier(contracts)
    result = verifier.verify_and_repair(base_valid_output, store, trace_writer)

    # Total must be repaired to match lines sum
    assert result["financial_resolution"]["recommended_refund_brl"] == 150.00

    trace_lines = trace_writer.path.read_text(encoding="utf-8").strip().splitlines()
    event = json.loads(trace_lines[-1])
    assert event["decision_code"] == "REPAIRED"


def test_verifier_enforces_no_action_consistency(
    base_valid_output: dict[str, Any],
    trace_writer: TraceWriter,
    contracts: Contracts,
) -> None:
    store = EvidenceStore("L3A_CASE_001", FakeGateway(), trace_writer)
    base_valid_output["assessment"]["case_status"] = "no_action"
    base_valid_output["financial_resolution"]["recommended_refund_brl"] = 100.00
    base_valid_output["financial_resolution"]["refund_lines"] = [
        {"reason_code": "INVALID", "amount_brl": 100.00, "entity_id": "ord_1"}
    ]
    base_valid_output["resolution_actions"] = ["issue_full_refund", "no_action_required"]

    verifier = Verifier(contracts)
    result = verifier.verify_and_repair(base_valid_output, store, trace_writer)

    # no_action must wipe refund and refund actions
    assert result["financial_resolution"]["recommended_refund_brl"] == 0.0
    assert result["financial_resolution"]["refund_lines"] == []
    assert "issue_full_refund" not in result["resolution_actions"]


def test_verifier_safely_downgrades_on_critical_violation(
    trace_writer: TraceWriter, contracts: Contracts
) -> None:
    store = EvidenceStore("L3A_CASE_001", FakeGateway(), trace_writer)
    # Corrupt data with invalid schema properties
    corrupt_output = {
        "case_id": "L3A_CASE_001",
        "invalid_junk_field": True,
    }

    verifier = Verifier(contracts)
    result = verifier.verify_and_repair(corrupt_output, store, trace_writer)

    assert result["assessment"]["primary_issue"] == "insufficient_evidence"
    assert result["assessment"]["case_status"] == "needs_investigation"
    assert result["financial_resolution"]["recommended_refund_brl"] == 0.0

    trace_lines = trace_writer.path.read_text(encoding="utf-8").strip().splitlines()
    event = json.loads(trace_lines[-1])
    assert event["decision_code"] == "DOWNGRADED"
