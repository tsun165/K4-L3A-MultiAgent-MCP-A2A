"""End-to-end solve_case() scenarios against a scripted FakeGateway.

These exercise the full coordinator -> specialists -> policy -> verifier
pipeline with plausible (but explicitly UNVERIFIED, see STANDARDS.md §4)
evidence shapes, so regressions in the business logic are caught before a
live MCP call is available. They do not assert exact field names are the
real ones -- only that the wiring, calibration and invariants hold.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from student_agent.contracts import Contracts
from student_agent.trace import TraceWriter
from student_agent.workflow import solve_case

ORDER_ID = "order-1"


class ScriptedGateway:
    """Returns per-tool canned evidence; unset tools return an empty domain-typed row."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict[str, Any]:
        self.calls.append(tool_name)
        data = self._responses.get(tool_name, [] if tool_name.endswith("_items") else {})
        ref = f"ev_{tool_name}_{case_id.lower()}_{len(self.calls):02d}xxxxxxxxxxxxxxxxxx"
        domain = {
            "get_order": "order",
            "get_order_items": "item",
            "get_sellers": "seller",
            "get_shipment_summary": "shipment",
            "get_order_payments": "payment",
            "get_refund_timeline": "refund",
            "get_policy": "policy",
        }.get(tool_name, "order")
        return {
            "schema_version": "day09-mcp-evidence-v1",
            "evidence_ref": ref,
            "result_hash": "sha256:" + "b" * 64,
            "domain": domain,
            "data": data,
            "warnings": [],
        }


@pytest.fixture
def contracts() -> Contracts:
    root = Path(__file__).resolve().parents[1]
    return Contracts(root / "contracts" / "schemas")


@pytest.fixture
def trace_writer(tmp_path: Path, contracts: Contracts) -> TraceWriter:
    return TraceWriter(tmp_path / "traces" / "trace.jsonl", contracts)


def _case(topic: str, claimed_order_id: str = ORDER_ID) -> dict[str, Any]:
    return {
        "case_id": "L3A_CASE_TEST",
        "opened_at": "2018-06-01T09:00:00-03:00",
        "customer_request": {
            "language": "vi",
            "message": "test",
            "claimed_order_id": claimed_order_id,
            "claims": [
                {"claim_id": "c-a", "topic": topic},
                {"claim_id": "c-b", "topic": "requested_full_refund"},
            ],
        },
        "policy_version": "EC_POLICY_V1",
    }


@pytest.mark.asyncio
async def test_canceled_order_paid_with_refund(
    trace_writer: TraceWriter, contracts: Contracts
) -> None:
    gw = ScriptedGateway(
        {
            "get_order": {"order_id": ORDER_ID, "order_status": "canceled"},
            "get_order_items": [
                {"order_item_id": "item-1", "seller_id": "seller-1", "price": "10.00"}
            ],
            "get_order_payments": [{"payment_sequential": 1, "payment_value": "100.00"}],
            "get_refund_timeline": {"order_id": ORDER_ID, "events": []},
            "get_policy": {"policy_version": "EC_POLICY_V1"},
        }
    )
    output = await solve_case(_case("canceled_order_paid"), gw, trace_writer)
    contracts.validate_output(output, "canceled_order_paid output")

    assert output["assessment"]["primary_issue"] == "canceled_order_paid"
    assert output["assessment"]["case_status"] == "action_required"
    assert output["financial_resolution"]["recommended_refund_brl"] == 100.0
    assert output["financial_resolution"]["refund_lines"][0]["amount_brl"] == 100.0
    assert output["root_cause_analysis"]["responsible_parties"]


@pytest.mark.asyncio
async def test_late_delivery_seller(trace_writer: TraceWriter, contracts: Contracts) -> None:
    gw = ScriptedGateway(
        {
            "get_shipment_summary": {
                "order_id": ORDER_ID,
                "order_status": "delivered",
                "delivered_carrier_at": "2018-06-10T00:00:00",
                "delivered_customer_at": "2018-06-15T00:00:00",
                "estimated_delivery_at": "2018-06-20T00:00:00",
                "shipping_limits": [
                    {
                        "order_item_id": "item-1",
                        "seller_id": "seller-1",
                        "shipping_limit_at": "2018-06-05T00:00:00",
                    }
                ],
            },
            "get_order_items": [
                {
                    "order_item_id": "item-1",
                    "seller_id": "seller-1",
                    "shipping_limit_date": "2018-06-05T00:00:00",
                    "freight_value": "15.00",
                }
            ],
        }
    )
    output = await solve_case(_case("late_delivery_seller"), gw, trace_writer)
    contracts.validate_output(output, "late_delivery_seller output")

    assert output["assessment"]["primary_issue"] == "late_delivery_seller"
    parties = output["root_cause_analysis"]["responsible_parties"]
    assert any(p["party_type"] == "seller" for p in parties)


@pytest.mark.asyncio
async def test_valid_split_payment_is_no_action(
    trace_writer: TraceWriter, contracts: Contracts
) -> None:
    gw = ScriptedGateway(
        {
            "get_order_payments": [
                {"payment_sequential": 1, "payment_value": "50.00", "payment_type": "voucher"},
                {"payment_sequential": 2, "payment_value": "50.00", "payment_type": "credit_card"},
            ],
            "get_order_items": [
                {"order_item_id": "item-1", "price": "90.00", "freight_value": "10.00"}
            ],
            "get_refund_timeline": {"order_id": ORDER_ID, "events": []},
        }
    )
    output = await solve_case(_case("valid_split_payment"), gw, trace_writer)
    contracts.validate_output(output, "valid_split_payment output")

    assert output["assessment"]["primary_issue"] == "valid_split_payment"
    assert output["assessment"]["case_status"] == "no_action"
    assert output["financial_resolution"]["recommended_refund_brl"] == 0.0
    assert output["financial_resolution"]["refund_lines"] == []


@pytest.mark.asyncio
async def test_refund_pending_uses_events_shape(
    trace_writer: TraceWriter, contracts: Contracts
) -> None:
    """get_refund_timeline returns {order_id, events:[...]}, not a list of refunds."""
    gw = ScriptedGateway(
        {
            "get_order_payments": [{"payment_sequential": 1, "payment_value": "89.00"}],
            "get_order_items": [
                {"order_item_id": "item-1", "price": "89.00", "freight_value": "0"}
            ],
            "get_refund_timeline": {
                "order_id": ORDER_ID,
                "events": [
                    {
                        "order_id": ORDER_ID,
                        "event_at": "2018-08-07T09:00:00-03:00",
                        "event_type": "refund_requested",
                        "amount_brl": "89.00",
                        "status": "pending",
                    }
                ],
            },
        }
    )
    output = await solve_case(_case("refund_pending"), gw, trace_writer)
    contracts.validate_output(output, "refund_pending output")

    assert output["assessment"]["primary_issue"] == "refund_pending"
    assert output["financial_resolution"]["recommended_refund_brl"] == 89.0


@pytest.mark.asyncio
async def test_unsupported_claim_when_status_contradicts(
    trace_writer: TraceWriter, contracts: Contracts
) -> None:
    gw = ScriptedGateway(
        {
            "get_order": {"order_id": ORDER_ID, "order_status": "delivered"},
            "get_order_items": [],
        }
    )
    output = await solve_case(_case("canceled_order_paid"), gw, trace_writer)
    contracts.validate_output(output, "unsupported_claim output")

    assert output["assessment"]["primary_issue"] == "unsupported_claim"
    assert output["assessment"]["case_status"] == "no_action"
    assert output["financial_resolution"]["recommended_refund_brl"] == 0.0
