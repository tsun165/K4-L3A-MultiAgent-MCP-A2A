from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..trace import TraceWriter
from . import vocab
from .base import EvidenceStore, SpecialistResult
from .tools import GET_ORDER_ITEMS, GET_ORDER_PAYMENTS, GET_REFUND_TIMELINE, PAYMENT_TOOLS

TWO_PLACES = Decimal("0.01")

PAYMENT_TOPICS = {
    "valid_split_payment",
    "payment_mismatch",
    "duplicate_charge",
    "refund_pending",
    "refund_failed",
}
# order-agent needs paid/refunded totals for these two even though it owns the topic.
ORDER_PAID_TOPICS = {"canceled_order_paid", "unavailable_order_paid"}


def _dec(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.00")
    try:
        return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    except Exception:  # noqa: BLE001 - defensive against unexpected evidence shapes
        return Decimal("0.00")


class PaymentAgent:
    """Specialist Agent for Payment and Refund domain.

    Primary Issues:
      - valid_split_payment, payment_mismatch, duplicate_charge
      - refund_pending, refund_failed
    Also computes findings["paid_total_brl"] / findings["refunded_total_brl"] consumed by
    OrderAgent.apply_payment_totals() for canceled_order_paid / unavailable_order_paid.

    Field names verified against a live MCP response (2026-09-25):
      - get_order_payments -> list of {order_id, payment_sequential, payment_type,
        payment_installments, payment_value}. Numeric fields are strings ("79.00").
      - get_refund_timeline -> a single object {order_id, events: [...]}, NOT a list.
        Each event is {order_id, event_at, event_type, amount_brl, status}, status in
        {"pending", "failed"} observed so far ("completed"/equivalent not yet seen live).
        The tool errors outright when an order has no refund history at all (observed on
        a live case) -- treated as "no refunds", see the try/except below.
    """

    name: str = "payment-agent"
    allowed_tools: frozenset[str] = PAYMENT_TOOLS

    async def run(
        self,
        case: dict[str, Any],
        store: EvidenceStore,
        trace: TraceWriter,
    ) -> SpecialistResult:
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
        needs_payment_data = bool(topics & (PAYMENT_TOPICS | ORDER_PAID_TOPICS))
        if not needs_payment_data:
            return

        order_id = case["customer_request"]["claimed_order_id"]

        payments_rec = await store.fetch(self.name, GET_ORDER_PAYMENTS, order_id=order_id)
        payments = payments_rec.data if isinstance(payments_rec.data, list) else []
        if payments:
            result.evidence_refs.append(payments_rec.evidence_ref)

        pay_refs: list[str] = []
        for idx, p in enumerate(payments):
            seq = p.get("payment_sequential", idx + 1)
            pay_refs.append(f"{order_id}_pay_{seq}")
        result.entities["payment_references"] = pay_refs

        total_paid = sum((_dec(p.get("payment_value")) for p in payments), Decimal("0.00"))

        # get_refund_timeline returns a single {order_id, events:[...]} object, and errors
        # outright when an order has no refund history at all (observed live) -- treat that
        # as "no refunds", not a crash: an unrelated tool failure here must not wipe out
        # paid_total_brl / the duplicate/mismatch classification below (STANDARDS §5:
        # not-found -> no retry, mark missing, never invented data).
        refund_events: list[dict[str, Any]] = []
        try:
            refunds_rec = await store.fetch(self.name, GET_REFUND_TIMELINE, order_id=order_id)
            refund_data = refunds_rec.data if isinstance(refunds_rec.data, dict) else {}
            events = refund_data.get("events")
            refund_events = events if isinstance(events, list) else []
            if refund_events:
                result.evidence_refs.append(refunds_rec.evidence_ref)
        except Exception as exc:  # noqa: BLE001 - degrade to "no refund data", don't crash
            result.errors.append(f"get_refund_timeline: {type(exc).__name__}: {exc}"[:200])

        completed_events = [e for e in refund_events if e.get("status") == "completed"]
        total_refunded = sum(
            (_dec(e.get("amount_brl")) for e in completed_events), Decimal("0.00")
        )
        result.findings["paid_total_brl"] = float(total_paid)
        result.findings["refunded_total_brl"] = float(total_refunded)

        claimed_topics = topics & PAYMENT_TOPICS
        if not claimed_topics:
            return

        # --- refund_failed / refund_pending take priority over other payment checks ---
        failed_events = [e for e in refund_events if e.get("status") == "failed"]
        if failed_events:
            self._conclude_refund(
                result,
                topic="refund_failed",
                claimed_topics=claimed_topics,
                events=failed_events,
                order_id=order_id,
                reason_code=vocab.FAILED_REFUND_REISSUE,
                cause_code=vocab.REFUND_PROCESSING_FAILED,
            )
            return

        pending_events = [e for e in refund_events if e.get("status") == "pending"]
        if pending_events:
            self._conclude_refund(
                result,
                topic="refund_pending",
                claimed_topics=claimed_topics,
                events=pending_events,
                order_id=order_id,
                reason_code=vocab.PENDING_REFUND_MONITORING,
                cause_code=vocab.REFUND_NOT_COMPLETED,
            )
            return

        # --- duplicate charge ---
        duplicate = self._detect_duplicate(payments)
        if duplicate is not None:
            amount = _dec(duplicate.get("payment_value"))
            self._set_claim_check(result, claimed_topics, "duplicate_charge")
            result.candidate_issues.append(("duplicate_charge", 0.85))
            result.cause_codes.append(vocab.DUPLICATE_PAYMENT_CAPTURED)
            result.responsible_parties.append(
                {"party_type": "payment_provider", "party_id": None}
            )
            if amount > 0:
                result.refund_lines.append(
                    {
                        "reason_code": vocab.DUPLICATE_CHARGE_REFUND,
                        "amount_brl": float(amount),
                        "entity_id": pay_refs[-1] if pay_refs else None,
                    }
                )
            return

        # --- payment amount mismatch ---
        items_rec = await store.fetch(self.name, GET_ORDER_ITEMS, order_id=order_id)
        items = items_rec.data if isinstance(items_rec.data, list) else []
        expected_total = sum(
            (_dec(i.get("price")) + _dec(i.get("freight_value")) for i in items),
            Decimal("0.00"),
        )
        if items:
            result.evidence_refs.append(items_rec.evidence_ref)

        diff = (total_paid - expected_total).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        if diff != 0 and "payment_mismatch" in claimed_topics:
            self._set_claim_check(result, claimed_topics, "payment_mismatch")
            result.candidate_issues.append(("payment_mismatch", 0.8))
            result.cause_codes.append(vocab.PAYMENT_AMOUNT_MISMATCH)
            result.responsible_parties.append(
                {"party_type": "payment_provider", "party_id": None}
            )
            if diff > 0:
                result.refund_lines.append(
                    {
                        "reason_code": vocab.PAYMENT_DIFFERENCE_REFUND,
                        "amount_brl": float(diff),
                        "entity_id": pay_refs[-1] if pay_refs else None,
                    }
                )
            # underpayment (diff < 0): flagged via cause/needs_investigation, no refund guessed
            return

        # --- valid split payment ---
        if "valid_split_payment" in claimed_topics:
            is_split = len(payments) > 1 and diff == 0
            self._set_claim_check(
                result, claimed_topics, "valid_split_payment" if is_split else None
            )
            if is_split:
                result.candidate_issues.append(("valid_split_payment", 0.9))
                result.cause_codes.append(vocab.SPLIT_PAYMENT_VALID)

    def _detect_duplicate(self, payments: list[dict[str, Any]]) -> dict[str, Any] | None:
        seen: dict[tuple[Any, ...], dict[str, Any]] = {}
        for p in payments:
            key = (p.get("payment_type"), str(_dec(p.get("payment_value"))), p.get(
                "payment_installments"
            ))
            if key in seen:
                return p
            seen[key] = p
        return None

    def _set_claim_check(
        self,
        result: SpecialistResult,
        claimed_topics: set[str],
        matched_topic: str | None,
    ) -> None:
        result.findings["claim_check"] = {
            t: (vocab.CLAIM_SUPPORTED if t == matched_topic else vocab.CLAIM_CONTRADICTED)
            for t in claimed_topics
        }

    def _conclude_refund(
        self,
        result: SpecialistResult,
        *,
        topic: str,
        claimed_topics: set[str],
        events: list[dict[str, Any]],
        order_id: str,
        reason_code: str | None,
        cause_code: str,
    ) -> None:
        self._set_claim_check(result, claimed_topics, topic)
        result.candidate_issues.append((topic, 0.85))
        result.cause_codes.append(cause_code)
        result.responsible_parties.append({"party_type": "payment_provider", "party_id": None})
        if reason_code is not None:
            # get_refund_timeline events carry no distinct id; entity_id falls back to order_id.
            for e in events:
                amount = _dec(e.get("amount_brl"))
                if amount > 0:
                    result.refund_lines.append(
                        {
                            "reason_code": reason_code,
                            "amount_brl": float(amount),
                            "entity_id": order_id,
                        }
                    )
