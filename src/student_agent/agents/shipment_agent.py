from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..trace import TraceWriter
from . import vocab
from .base import EvidenceStore, SpecialistResult
from .tools import GET_ORDER, GET_ORDER_ITEMS, GET_SHIPMENT_SUMMARY, SHIPMENT_TOOLS

SHIPMENT_TOPICS = {"late_delivery_seller", "late_delivery_logistics"}


class ShipmentAgent:
    """Specialist Agent for Shipment and Delivery domain.

    Owner: Sơn (tsun165)
    Primary Issues:
      - late_delivery_seller     (seller handed over to carrier after shipping_limit)
      - late_delivery_logistics  (seller on time, customer received after estimated date)
    Responsible for affected_entities:
      - shipment_ids (MCP exposes no shipment id, so this stays empty)
    """

    name: str = "shipment-agent"
    allowed_tools: frozenset[str] = SHIPMENT_TOOLS

    async def run(
        self,
        case: dict[str, Any],
        store: EvidenceStore,
        trace: TraceWriter,
    ) -> SpecialistResult:
        """Compare carrier handoff vs shipping limits and delivery vs estimated date."""
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
        topics = {claim.get("topic") for claim in case["customer_request"].get("claims", [])}
        claimed_topics = topics & SHIPMENT_TOPICS
        if not claimed_topics:
            return

        claimed_id = case["customer_request"]["claimed_order_id"]
        summary_rec = await store.fetch(self.name, GET_SHIPMENT_SUMMARY, order_id=claimed_id)
        summary = summary_rec.data if isinstance(summary_rec.data, dict) else {}
        if not summary.get("order_id") or summary["order_id"] != claimed_id:
            result.errors.append(vocab.EVIDENCE_UNAVAILABLE)
            return
        order_id = summary["order_id"]

        carrier_at = _parse(summary.get("delivered_carrier_at"))
        customer_at = _parse(summary.get("delivered_customer_at"))
        estimated_at = _parse(summary.get("estimated_delivery_at"))
        result.findings["order_status"] = summary.get("order_status")
        if summary.get("order_status") != "delivered" or carrier_at is None or customer_at is None:
            # Canceled / in-transit orders cannot be judged as late deliveries.
            result.findings["delivery_decision"] = "NOT_DELIVERED"
            return

        result.entities["order_ids"].append(order_id)
        result.evidence_refs.append(summary_rec.evidence_ref)

        shipping_limits = summary.get("shipping_limits") or []
        limits = select_shipping_limits(shipping_limits, case.get("opened_at"))
        conflicts = [item_id for item_id, row in limits.items() if row["conflict"]]
        if conflicts:
            # Conflicting limit rows: the purchase date bounds which limit is plausible.
            order_rec = await store.fetch(self.name, GET_ORDER, order_id=order_id)
            order = order_rec.data if isinstance(order_rec.data, dict) else {}
            purchased_at = order.get("order_purchase_timestamp")
            if purchased_at:
                result.evidence_refs.append(order_rec.evidence_ref)
                limits = select_shipping_limits(
                    shipping_limits, case.get("opened_at"), purchased_at
                )
        result.findings["shipping_limit_conflicts"] = conflicts

        late_items = [
            item_id for item_id, row in limits.items() if carrier_at > _parse(row["limit"])
        ]
        if late_items:
            decision = "late_delivery_seller"
        elif estimated_at is not None and customer_at > estimated_at:
            decision = "late_delivery_logistics"
        else:
            decision = None
        result.findings["delivery_decision"] = decision or "ON_TIME"
        result.findings["claim_check"] = {
            t: vocab.CLAIM_SUPPORTED if t == decision else vocab.CLAIM_CONTRADICTED
            for t in claimed_topics
        }
        if decision is None:
            return

        result.candidate_issues.append((decision, 0.9))
        refund_items = late_items if late_items else list(limits)
        freight = await self._freight_by_item(store, order_id, limits, result)
        for item_id in refund_items:
            seller_id = limits[item_id]["seller_id"]
            result.entities["item_ids"].append(item_id)
            if seller_id and seller_id not in result.entities["seller_ids"]:
                result.entities["seller_ids"].append(seller_id)
            if item_id in freight:
                # EC_POLICY_V1: late delivery -> refund_freight
                result.refund_lines.append(
                    {
                        "reason_code": vocab.LATE_DELIVERY_COMPENSATION,
                        "amount_brl": float(freight[item_id]),
                        "entity_id": item_id,
                    }
                )

        if decision == "late_delivery_seller":
            result.cause_codes.append(vocab.SELLER_LATE_HANDOVER)
            result.responsible_parties.extend(
                {"party_type": "seller", "party_id": seller_id}
                for seller_id in result.entities["seller_ids"]
            )
        else:
            result.cause_codes.append(vocab.CARRIER_LATE_DELIVERY)
            result.responsible_parties.append(
                {"party_type": "logistics_provider", "party_id": None}
            )

    async def _freight_by_item(
        self,
        store: EvidenceStore,
        order_id: str,
        limits: dict[str, dict[str, Any]],
        result: SpecialistResult,
    ) -> dict[str, Decimal]:
        """Freight of the item row whose shipping_limit_date matches the selected limit."""
        items_rec = await store.fetch(self.name, GET_ORDER_ITEMS, order_id=order_id)
        items = items_rec.data if isinstance(items_rec.data, list) else []
        freight: dict[str, Decimal] = {}
        for item in items:
            row = limits.get(item.get("order_item_id"))
            if (
                row
                and item.get("shipping_limit_date") == row["limit"]
                and item.get("freight_value")
            ):
                freight[item["order_item_id"]] = Decimal(str(item["freight_value"])).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
        if freight:
            result.evidence_refs.append(items_rec.evidence_ref)
        return freight


def select_shipping_limits(
    shipping_limits: list[dict[str, Any]],
    opened_at: str | None,
    purchased_at: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Pick one authoritative shipping limit per item.

    MCP may return several rows for the same order_item_id with different limits. A real
    limit lies between the purchase and the case opening (seen in data: noise rows dated
    after opened_at, or before the purchase), so prefer the earliest limit in that window
    (fallback: earliest overall).
    """
    opened = _parse(opened_at)
    purchased = _parse(purchased_at)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in shipping_limits:
        if row.get("order_item_id") and _parse(row.get("shipping_limit_at")) is not None:
            grouped.setdefault(row["order_item_id"], []).append(row)

    def plausible(row: dict[str, Any]) -> bool:
        limit = _parse(row["shipping_limit_at"])
        return (purchased is None or limit >= purchased) and (opened is None or limit <= opened)

    selected: dict[str, dict[str, Any]] = {}
    for item_id, rows in grouped.items():
        rows.sort(key=lambda r: _parse(r["shipping_limit_at"]))
        chosen = ([r for r in rows if plausible(r)] or rows)[0]
        selected[item_id] = {
            "limit": chosen["shipping_limit_at"],
            "seller_id": chosen.get("seller_id"),
            "conflict": len({r["shipping_limit_at"] for r in rows}) > 1,
        }
    return selected


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
