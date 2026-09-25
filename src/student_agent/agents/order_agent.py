from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..trace import TraceWriter
from .base import EvidenceStore, SpecialistResult

# order_status value -> (primary_issue, cause_code, responsible party_type) per EC_POLICY_V1
PAID_STATUS_RULES: dict[str, tuple[str, str, str]] = {
    "canceled": ("canceled_order_paid", "ORDER_CANCELED_AFTER_PAYMENT", "platform"),
    "unavailable": ("unavailable_order_paid", "ITEM_UNAVAILABLE_AFTER_PAYMENT", "seller"),
}
ORDER_TOPICS = {"canceled_order_paid", "unavailable_order_paid"}


class OrderAgent:
    """Specialist Agent for Order, Item, Seller and Product domain.

    Owner: Sơn (tsun165)
    Primary Issues:
      - canceled_order_paid
      - unavailable_order_paid
    Responsible for affected_entities:
      - order_ids
      - item_ids
      - seller_ids
    """

    name: str = "order-agent"
    allowed_tools: set[str] = {
        "get_order",
        "get_order_items",
        "get_sellers",
    }

    async def run(
        self,
        case: dict[str, Any],
        store: EvidenceStore,
        trace: TraceWriter,
    ) -> SpecialistResult:
        """Verify order status and collect order/item/seller entities from MCP evidence.

        The paid/refunded amounts belong to payment-agent; the coordinator passes them in
        via ``apply_payment_totals`` to finish the canceled/unavailable decision.
        """
        del trace  # tool_result_consumed is emitted by EvidenceStore.fetch
        result = SpecialistResult(actor=self.name)
        order_id = case["customer_request"]["claimed_order_id"]
        topics = {claim.get("topic") for claim in case["customer_request"].get("claims", [])}

        order_rec = await store.fetch(self.name, "get_order", order_id=order_id)
        order = order_rec.data if isinstance(order_rec.data, dict) else {}
        if order.get("order_id") != order_id:
            result.errors.append("ORDER_NOT_CONFIRMED")
            return result
        result.entities["order_ids"].append(order_id)
        result.evidence_refs.append(order_rec.evidence_ref)

        items_rec = await store.fetch(self.name, "get_order_items", order_id=order_id)
        items = items_rec.data if isinstance(items_rec.data, list) else []
        for item in items:
            _add(result.entities["item_ids"], item.get("order_item_id"))
            _add(result.entities["seller_ids"], item.get("seller_id"))
        if items:
            result.evidence_refs.append(items_rec.evidence_ref)

        status = order.get("order_status")
        result.findings["order_status"] = status

        rule = PAID_STATUS_RULES.get(status)
        if rule is not None and topics & ORDER_TOPICS:
            issue, cause_code, party_type = rule
            result.candidate_issues.append((issue, 0.9))
            result.cause_codes.append(cause_code)
            if party_type == "seller":
                sellers_rec = await store.fetch(self.name, "get_sellers", order_id=order_id)
                sellers = sellers_rec.data if isinstance(sellers_rec.data, list) else []
                seller_ids = [s["seller_id"] for s in sellers if s.get("seller_id")]
                if seller_ids:
                    result.evidence_refs.append(sellers_rec.evidence_ref)
                for seller_id in seller_ids:
                    _add(result.entities["seller_ids"], seller_id)
                    result.responsible_parties.append(
                        {"party_type": "seller", "party_id": seller_id}
                    )
            else:
                result.responsible_parties.append({"party_type": party_type, "party_id": None})
            result.findings["refund_pending_payment_totals"] = True
        elif topics & ORDER_TOPICS:
            # Customer claims cancel/unavailable but the authoritative status disagrees.
            result.candidate_issues.append(("unsupported_claim", 0.7))
            result.cause_codes.append("ORDER_STATUS_CONTRADICTS_CLAIM")
            result.responsible_parties.append({"party_type": "customer", "party_id": None})
        return result

    def apply_payment_totals(self, result: SpecialistResult, payment: SpecialistResult) -> None:
        """Finish canceled/unavailable decision with payment-agent's paid/refunded totals."""
        if not result.findings.pop("refund_pending_payment_totals", False):
            return
        paid = payment.findings.get("paid_total_brl")
        refunded = payment.findings.get("refunded_total_brl") or 0
        if paid is None:
            return  # keep status-based candidate; payment evidence is missing
        outstanding = Decimal(str(paid)) - Decimal(str(refunded))
        if outstanding <= 0:
            # Nothing left to refund: not a "paid" cancellation any more.
            result.candidate_issues = [(issue, 0.4) for issue, _ in result.candidate_issues]
            return
        result.refund_lines.append(
            {
                "reason_code": "issue_refund",
                "amount_brl": float(outstanding),
                "entity_id": result.entities["order_ids"][0],
            }
        )


def _add(values: list[str], value: Any) -> None:
    if value and value not in values:
        values.append(value)
