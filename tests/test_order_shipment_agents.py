from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from student_agent.agents import vocab
from student_agent.agents.base import EvidenceStore, SpecialistResult
from student_agent.agents.order_agent import OrderAgent
from student_agent.agents.shipment_agent import ShipmentAgent, select_shipping_limits
from student_agent.agents.tools import ALL_TOOLS
from student_agent.contracts import Contracts
from student_agent.trace import TraceWriter

ORDER_ID = "e2a03ccf5ea816036608b2d8c3ab8e60"
ITEM_ID = "item-e2a03ccf5ea8"
SELLER_ID = "seller-e2a03ccf5ea8"

# Shapes copied from real MCP responses (L3A_CASE_001 / L3A_CASE_003), values adjusted per test.
ORDER = {
    "order_id": ORDER_ID,
    "customer_id": "customer-row-e2a03ccf5ea8",
    "order_status": "delivered",
    "order_purchase_timestamp": "2018-02-19T09:00:00-03:00",
    "order_approved_at": "2018-02-19T10:00:00-03:00",
    "order_delivered_carrier_date": "2018-02-21T09:00:00-03:00",
    "order_delivered_customer_date": "2018-02-28T09:00:00-03:00",
    "order_estimated_delivery_date": "2018-03-01T09:00:00-03:00",
}
ITEMS = [
    {
        "order_id": ORDER_ID,
        "order_item_id": ITEM_ID,
        "product_id": "product-e2a03ccf5ea8",
        "seller_id": SELLER_ID,
        "shipping_limit_date": "2018-02-22T09:00:00-03:00",
        "price": "79.00",
        "freight_value": "18.00",
    },
    {
        "order_id": ORDER_ID,
        "order_item_id": ITEM_ID,
        "product_id": "product-e2a03ccf5ea8",
        "seller_id": SELLER_ID,
        "shipping_limit_date": "2018-03-12T09:00:00-03:00",
        "price": "79.00",
        "freight_value": "10.00",
    },
]
SELLERS = [{"seller_id": SELLER_ID, "seller_city": "sao_paulo", "seller_state": "SP"}]
SHIPMENT = {
    "order_id": ORDER_ID,
    "order_status": "delivered",
    "delivered_carrier_at": "2018-02-21T09:00:00-03:00",
    "delivered_customer_at": "2018-02-28T09:00:00-03:00",
    "estimated_delivery_at": "2018-03-01T09:00:00-03:00",
    "shipping_limits": [
        {"order_item_id": ITEM_ID, "seller_id": SELLER_ID, "shipping_limit_at": row}
        for row in ("2018-02-22T09:00:00-03:00", "2018-03-12T09:00:00-03:00")
    ],
    "events": [],
}
DOMAINS = {
    "get_order": "order",
    "get_order_items": "item",
    "get_sellers": "seller",
    "get_shipment_summary": "shipment",
}


class FakeGateway:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.calls: list[str] = []

    async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict[str, Any]:
        self.calls.append(tool_name)
        return {
            "schema_version": "day09-mcp-evidence-v1",
            "evidence_ref": f"ev_{tool_name}_{case_id}_0000000000",
            "result_hash": "sha256:" + "b" * 64,
            "domain": DOMAINS[tool_name],
            "data": copy.deepcopy(self.data[tool_name]),
            "warnings": [],
        }


def _case(topic: str, opened_at: str) -> dict[str, Any]:
    return {
        "case_id": "L3A_CASE_900",
        "opened_at": opened_at,
        "customer_request": {
            "claimed_order_id": ORDER_ID,
            "claims": [
                {"claim_id": "claim-a", "topic": topic},
                {"claim_id": "claim-b", "topic": "requested_full_refund"},
            ],
        },
        "policy_version": "EC_POLICY_V1",
    }


def _ref(tool_name: str) -> str:
    return f"ev_{tool_name}_L3A_CASE_900_0000000000"


@pytest.fixture
def trace(tmp_path: Path) -> TraceWriter:
    root = Path(__file__).resolve().parents[1]
    return TraceWriter(tmp_path / "trace.jsonl", Contracts(root / "contracts" / "schemas"))


async def _run(
    agent: Any,
    topic: str,
    trace: TraceWriter,
    opened_at: str = "2018-03-03T09:00:00-03:00",
    **overrides: Any,
) -> SpecialistResult:
    data = {
        "get_order": ORDER,
        "get_order_items": ITEMS,
        "get_sellers": SELLERS,
        "get_shipment_summary": SHIPMENT,
    }
    for tool, patch in overrides.items():
        data[tool] = {**data[tool], **patch} if isinstance(patch, dict) else patch
    gateway = FakeGateway(data)
    store = EvidenceStore("L3A_CASE_900", gateway, trace)
    store.register_actor_tools(agent.name, agent.allowed_tools)
    return await agent.run(_case(topic, opened_at), store, trace)


# --- order-agent ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_canceled_order_paid_refunds_outstanding_payment(trace: TraceWriter) -> None:
    agent = OrderAgent()
    res = await _run(agent, "canceled_order_paid", trace, get_order={"order_status": "canceled"})

    assert res.candidate_issues == [("canceled_order_paid", 0.9)]
    assert res.cause_codes == ["ORDER_CANCELED_AFTER_PAYMENT"]
    assert res.responsible_parties == [{"party_type": "platform", "party_id": None}]
    assert res.entities["order_ids"] == [ORDER_ID]
    assert res.entities["item_ids"] == [ITEM_ID]
    assert res.entities["seller_ids"] == [SELLER_ID]
    assert res.evidence_refs == [_ref("get_order"), _ref("get_order_items")]
    assert res.findings["claim_check"] == {"canceled_order_paid": vocab.CLAIM_SUPPORTED}

    payment = SpecialistResult(
        actor="payment-agent", findings={"paid_total_brl": 97.0, "refunded_total_brl": 0}
    )
    agent.apply_payment_totals(res, payment)
    assert res.refund_lines == [
        {"reason_code": "CANCELED_ORDER_REFUND", "amount_brl": 97.0, "entity_id": ORDER_ID}
    ]


@pytest.mark.asyncio
async def test_canceled_order_already_refunded_lowers_confidence(trace: TraceWriter) -> None:
    agent = OrderAgent()
    res = await _run(agent, "canceled_order_paid", trace, get_order={"order_status": "canceled"})
    payment = SpecialistResult(
        actor="payment-agent", findings={"paid_total_brl": 97.0, "refunded_total_brl": 97.0}
    )
    agent.apply_payment_totals(res, payment)
    assert res.refund_lines == []
    assert res.candidate_issues == [("canceled_order_paid", 0.4)]


@pytest.mark.asyncio
async def test_unavailable_order_paid_blames_seller(trace: TraceWriter) -> None:
    res = await _run(
        OrderAgent(), "unavailable_order_paid", trace, get_order={"order_status": "unavailable"}
    )
    assert res.candidate_issues == [("unavailable_order_paid", 0.9)]
    assert res.cause_codes == ["ORDER_UNAVAILABLE_AFTER_PAYMENT"]
    assert res.responsible_parties == [{"party_type": "seller", "party_id": SELLER_ID}]
    assert _ref("get_sellers") in res.evidence_refs


@pytest.mark.asyncio
async def test_cancel_claim_on_delivered_order_is_contradicted(trace: TraceWriter) -> None:
    # unsupported_claim is the coordinator's call; the agent only reports the contradiction.
    res = await _run(OrderAgent(), "canceled_order_paid", trace)
    assert res.candidate_issues == []
    assert res.findings["claim_check"] == {"canceled_order_paid": vocab.CLAIM_CONTRADICTED}
    assert res.evidence_refs == [_ref("get_order"), _ref("get_order_items")]


@pytest.mark.asyncio
async def test_missing_payment_totals_adds_no_refund(trace: TraceWriter) -> None:
    agent = OrderAgent()
    res = await _run(agent, "canceled_order_paid", trace, get_order={"order_status": "canceled"})
    agent.apply_payment_totals(res, SpecialistResult(actor="payment-agent"))
    assert res.refund_lines == []
    assert res.candidate_issues == [("canceled_order_paid", 0.9)]


@pytest.mark.asyncio
async def test_order_agent_stays_silent_on_other_topics(trace: TraceWriter) -> None:
    res = await _run(
        OrderAgent(), "duplicate_charge", trace, get_order={"order_status": "canceled"}
    )
    assert res.candidate_issues == []
    assert res.entities["order_ids"] == [ORDER_ID]


@pytest.mark.asyncio
async def test_order_agent_ignores_unconfirmed_order(trace: TraceWriter) -> None:
    res = await _run(OrderAgent(), "canceled_order_paid", trace, get_order={"order_id": "other"})
    assert res.errors == ["EVIDENCE_UNAVAILABLE"]
    assert res.evidence_refs == []
    assert res.entities["order_ids"] == []


# --- shipment-agent ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_late_delivery_seller_uses_limit_known_at_case_open(trace: TraceWriter) -> None:
    # Real L3A_CASE_003 timeline: handoff 02-26 after limit 02-22 (row 03-12 is noise).
    res = await _run(
        ShipmentAgent(),
        "late_delivery_seller",
        trace,
        get_shipment_summary={
            "delivered_carrier_at": "2018-02-26T09:00:00-03:00",
            "delivered_customer_at": "2018-03-05T09:00:00-03:00",
        },
    )
    assert res.candidate_issues == [("late_delivery_seller", 0.9)]
    assert res.cause_codes == ["SELLER_LATE_HANDOVER"]
    assert res.responsible_parties == [{"party_type": "seller", "party_id": SELLER_ID}]
    assert res.refund_lines == [
        {"reason_code": "LATE_DELIVERY_COMPENSATION", "amount_brl": 18.0, "entity_id": ITEM_ID}
    ]
    assert res.entities["item_ids"] == [ITEM_ID]
    assert res.entities["shipment_ids"] == []
    assert res.evidence_refs == [
        _ref("get_shipment_summary"),
        _ref("get_order"),
        _ref("get_order_items"),
    ]
    assert res.findings["shipping_limit_conflicts"] == [ITEM_ID]


@pytest.mark.asyncio
async def test_limit_before_purchase_is_ignored(trace: TraceWriter) -> None:
    # Real L3A_CASE_004 pattern: noise limit row dated before the purchase.
    limits = [
        {"order_item_id": ITEM_ID, "seller_id": SELLER_ID, "shipping_limit_at": row}
        for row in ("2018-03-26T09:00:00-03:00", "2018-02-11T09:00:00-03:00")
    ]
    res = await _run(
        ShipmentAgent(),
        "late_delivery_logistics",
        trace,
        opened_at="2018-04-04T09:00:00-03:00",
        get_order={"order_purchase_timestamp": "2018-03-20T09:00:00-03:00"},
        get_order_items=[],
        get_shipment_summary={
            "delivered_carrier_at": "2018-03-25T09:00:00-03:00",
            "delivered_customer_at": "2018-04-07T09:00:00-03:00",
            "estimated_delivery_at": "2018-04-02T09:00:00-03:00",
            "shipping_limits": limits,
        },
    )
    assert res.candidate_issues == [("late_delivery_logistics", 0.9)]


@pytest.mark.asyncio
async def test_late_delivery_logistics_when_seller_on_time(trace: TraceWriter) -> None:
    res = await _run(
        ShipmentAgent(),
        "late_delivery_logistics",
        trace,
        get_shipment_summary={"delivered_customer_at": "2018-03-05T09:00:00-03:00"},
    )
    assert res.candidate_issues == [("late_delivery_logistics", 0.9)]
    assert res.cause_codes == ["CARRIER_LATE_DELIVERY"]
    assert res.responsible_parties == [{"party_type": "logistics_provider", "party_id": None}]
    assert res.refund_lines == [
        {"reason_code": "LATE_DELIVERY_COMPENSATION", "amount_brl": 18.0, "entity_id": ITEM_ID}
    ]


@pytest.mark.asyncio
async def test_seller_late_wins_over_carrier_late(trace: TraceWriter) -> None:
    res = await _run(
        ShipmentAgent(),
        "late_delivery_logistics",
        trace,
        get_shipment_summary={
            "delivered_carrier_at": "2018-02-26T09:00:00-03:00",
            "delivered_customer_at": "2018-03-05T09:00:00-03:00",
        },
    )
    assert res.candidate_issues == [("late_delivery_seller", 0.9)]
    assert res.findings["claim_check"] == {"late_delivery_logistics": vocab.CLAIM_CONTRADICTED}


@pytest.mark.asyncio
async def test_on_time_delivery_contradicts_claim(trace: TraceWriter) -> None:
    res = await _run(ShipmentAgent(), "late_delivery_logistics", trace)
    assert res.candidate_issues == []
    assert res.findings["claim_check"] == {"late_delivery_logistics": vocab.CLAIM_CONTRADICTED}
    assert res.refund_lines == []
    assert _ref("get_shipment_summary") in res.evidence_refs


@pytest.mark.asyncio
async def test_canceled_order_is_not_judged_late(trace: TraceWriter) -> None:
    # Real L3A_CASE_001: canceled, never delivered, but has a noisy "delivered_late" event.
    res = await _run(
        ShipmentAgent(),
        "late_delivery_seller",
        trace,
        get_shipment_summary={"order_status": "canceled", "delivered_customer_at": None},
    )
    assert res.candidate_issues == []
    assert res.evidence_refs == []
    assert res.findings["delivery_decision"] == "NOT_DELIVERED"


@pytest.mark.asyncio
async def test_shipment_agent_skips_non_delivery_topics(trace: TraceWriter) -> None:
    agent = ShipmentAgent()
    res = await _run(agent, "payment_mismatch", trace)
    assert res.candidate_issues == []
    assert res.evidence_refs == []


def test_select_shipping_limits_falls_back_to_earliest() -> None:
    rows = [
        {
            "order_item_id": "i1",
            "seller_id": "s1",
            "shipping_limit_at": "2018-05-01T00:00:00-03:00",
        },
        {
            "order_item_id": "i1",
            "seller_id": "s1",
            "shipping_limit_at": "2018-04-01T00:00:00-03:00",
        },
    ]
    selected = select_shipping_limits(rows, "2018-01-01T00:00:00-03:00")
    assert selected["i1"]["limit"] == "2018-04-01T00:00:00-03:00"
    assert selected["i1"]["conflict"] is True


@pytest.mark.asyncio
async def test_specialists_do_not_raise_on_tool_error(trace: TraceWriter) -> None:
    class BrokenGateway:
        async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict[str, Any]:
            raise RuntimeError("MCP tool failed: not found")

    for agent in (OrderAgent(), ShipmentAgent()):
        store = EvidenceStore("L3A_CASE_900", BrokenGateway(), trace)
        res = await agent.run(
            _case("late_delivery_seller", "2018-03-03T09:00:00-03:00"), store, trace
        )
        assert res.errors and res.candidate_issues == [] and res.evidence_refs == []


def test_tool_registry_matches_notes() -> None:
    notes = (Path(__file__).resolve().parents[1] / "notes" / "mcp-tools.md").read_text(
        encoding="utf-8"
    )
    missing = sorted(name for name in ALL_TOOLS if f"`{name}`" not in notes)
    assert missing == []
    for agent in (OrderAgent(), ShipmentAgent()):
        assert agent.allowed_tools <= ALL_TOOLS
