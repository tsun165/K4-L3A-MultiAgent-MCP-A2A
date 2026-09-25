from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..trace import TraceWriter
from . import vocab
from .base import EvidenceStore, SpecialistResult
from .tools import GET_ORDER, GET_ORDER_ITEMS, GET_SELLERS, ORDER_TOOLS

# order_status -> (primary_issue, cause_code, refund reason_code, responsible party_type)
# Responsible parties follow EC_POLICY_V1 (get_policy).
PAID_STATUS_RULES: dict[str, tuple[str, str, str, str]] = {
    "canceled": (
        "canceled_order_paid",
        vocab.ORDER_CANCELED_AFTER_PAYMENT,
        vocab.CANCELED_ORDER_REFUND,
        "platform",
    ),
    "unavailable": (
        "unavailable_order_paid",
        vocab.ORDER_UNAVAILABLE_AFTER_PAYMENT,
        vocab.UNAVAILABLE_ORDER_REFUND,
        "seller",
    ),
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
    allowed_tools: frozenset[str] = ORDER_TOOLS

    async def run(
        self,
        case: dict[str, Any],
        store: EvidenceStore,
        trace: TraceWriter,
    ) -> SpecialistResult:
        """Verify order status and collect order/item/seller entities from MCP evidence.

        The paid/refunded amounts belong to payment-agent; the coordinator passes them in
        via ``apply_payment_totals`` to finish the canceled/unavailable refund.
        """
        del trace  # tool_result_consumed is emitted by EvidenceStore.fetch
        result = SpecialistResult(actor=self.name)
        try:
            await self._investigate(case, store, result)
        except Exception as exc:  # specialists never raise (STANDARDS §5)
            result.errors.append(f"{type(exc).__name__}: {exc}"[:200])
        return result

    async def _investigate(
        self, case: dict[str, Any], store: EvidenceStore, result: SpecialistResult
    ) -> None:
        claimed_id = case["customer_request"]["claimed_order_id"]
        topics = {claim.get("topic") for claim in case["customer_request"].get("claims", [])}

        order_rec = await store.fetch(self.name, GET_ORDER, order_id=claimed_id)
        order = order_rec.data if isinstance(order_rec.data, dict) else {}
        if not order.get("order_id") or order["order_id"] != claimed_id:
            result.errors.append(vocab.EVIDENCE_UNAVAILABLE)
            return
        order_id = order["order_id"]
        result.entities["order_ids"].append(order_id)
        result.evidence_refs.append(order_rec.evidence_ref)

        items_rec = await store.fetch(self.name, GET_ORDER_ITEMS, order_id=order_id)
        items = items_rec.data if isinstance(items_rec.data, list) else []
        for item in items:
            _add(result.entities["item_ids"], item.get("order_item_id"))
            _add(result.entities["seller_ids"], item.get("seller_id"))
        if items:
            result.evidence_refs.append(items_rec.evidence_ref)

        status = order.get("order_status")
        result.findings["order_status"] = status
        claimed_topics = topics & ORDER_TOPICS
        if not claimed_topics:
            # Canceled orders also appear in refund_pending/refund_failed cases, which
            # belong to payment-agent; only judge cancel/unavailable when claimed.
            return
        rule = PAID_STATUS_RULES.get(status)
        if rule is None:
            # Claim of cancel/unavailable contradicted by the authoritative status.
            result.findings["claim_check"] = {t: vocab.CLAIM_CONTRADICTED for t in claimed_topics}
            return

        issue, cause_code, reason_code, party_type = rule
        result.findings["claim_check"] = {
            t: vocab.CLAIM_SUPPORTED if t == issue else vocab.CLAIM_CONTRADICTED
            for t in claimed_topics
        }
        result.findings["refund_reason_code"] = reason_code
        result.candidate_issues.append((issue, 0.9))
        result.cause_codes.append(cause_code)
        if party_type != "seller":
            result.responsible_parties.append({"party_type": party_type, "party_id": None})
            return

        sellers_rec = await store.fetch(self.name, GET_SELLERS, order_id=order_id)
        sellers = sellers_rec.data if isinstance(sellers_rec.data, list) else []
        seller_ids = [s["seller_id"] for s in sellers if s.get("seller_id")]
        if seller_ids:
            result.evidence_refs.append(sellers_rec.evidence_ref)
        for seller_id in seller_ids:
            _add(result.entities["seller_ids"], seller_id)
            result.responsible_parties.append({"party_type": "seller", "party_id": seller_id})

    def apply_payment_totals(self, result: SpecialistResult, payment: SpecialistResult) -> None:
        """Add the canceled/unavailable refund line from payment-agent's paid/refunded totals.

        Refund = paid - already refunded (STANDARDS §8). Without payment totals nothing
        is added, so no amount is ever guessed.
        """
        reason_code = result.findings.get("refund_reason_code")
        paid = payment.findings.get("paid_total_brl")
        if reason_code is None or paid is None or not result.entities["order_ids"]:
            return
        refunded = payment.findings.get("refunded_total_brl") or 0
        outstanding = (Decimal(str(paid)) - Decimal(str(refunded))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if outstanding <= 0:
            # Already fully refunded: the "paid" part of the issue no longer holds.
            result.candidate_issues = [(issue, 0.4) for issue, _ in result.candidate_issues]
            return
        result.refund_lines.append(
            {
                "reason_code": reason_code,
                "amount_brl": float(outstanding),
                "entity_id": result.entities["order_ids"][0],
            }
        )


def _add(values: list[str], value: Any) -> None:
    if value and value not in values:
        values.append(value)
