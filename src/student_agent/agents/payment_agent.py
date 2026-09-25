"""Payment specialist agent — analyses payment/refund evidence for a case.

Responsibilities
----------------
- Fetch payment & refund evidence via MCP for the case's order.
- Classify into one of the payment-related primary issues:
    valid_split_payment, payment_mismatch, duplicate_charge,
    refund_pending, refund_failed.
- Calculate financial resolution with ``Decimal`` arithmetic, rounded
  to 2 decimal places.
- Provide paid / refunded amounts for ``canceled_order_paid`` rule
  consumed by the order agent.
- Emit ``tool_result_consumed`` trace events for every MCP call.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..mcp_gateway import EvidenceGateway
from ..trace import TraceWriter
from .specialist_result import RefundLine, SpecialistResult

# ------------------------------------------------------------------ #
# Constants
# ------------------------------------------------------------------ #

_TWO_PLACES = Decimal("0.01")
ACTOR = "payment-agent"


def _to_decimal(value: Any) -> Decimal:
    """Safely convert a numeric value to Decimal with 2-place rounding."""
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


# ------------------------------------------------------------------ #
# PaymentAgent
# ------------------------------------------------------------------ #


class PaymentAgent:
    """Specialist agent for payment and refund analysis.

    Usage
    -----
    >>> agent = PaymentAgent(gateway, trace)
    >>> result = await agent.analyse(case)
    """

    def __init__(self, gateway: EvidenceGateway, trace: TraceWriter) -> None:
        self._gw = gateway
        self._trace = trace

    # -------------------------------------------------------------- #
    # Public API
    # -------------------------------------------------------------- #

    async def analyse(self, case: dict[str, Any]) -> SpecialistResult:
        """Run the full payment analysis pipeline and return a ``SpecialistResult``."""
        case_id: str = case["case_id"]
        order_id: str = case["customer_request"]["claimed_order_id"]

        # 1. Gather evidence -------------------------------------------------
        payments, payment_refs, payment_ev_refs = await self._fetch_payments(
            case_id, order_id
        )
        items, item_refs = await self._fetch_items(case_id, order_id)
        refunds, refund_ev_refs = await self._fetch_refunds(case_id, order_id)

        all_evidence_refs = payment_ev_refs + item_refs + refund_ev_refs

        # 2. Calculate totals ------------------------------------------------
        total_paid = sum(
            (_to_decimal(p.get("payment_value")) for p in payments),
            start=Decimal("0.00"),
        )
        total_price = sum(
            (_to_decimal(i.get("price")) for i in items),
            start=Decimal("0.00"),
        )
        total_freight = sum(
            (_to_decimal(i.get("freight_value")) for i in items),
            start=Decimal("0.00"),
        )
        expected_total = (total_price + total_freight).quantize(
            _TWO_PLACES, rounding=ROUND_HALF_UP
        )

        total_refunded = sum(
            (
                _to_decimal(r.get("refund_amount"))
                for r in refunds
                if r.get("refund_status") == "completed"
            ),
            start=Decimal("0.00"),
        )

        # 3. Classify issue ---------------------------------------------------
        result = self._classify(
            case_id=case_id,
            payments=payments,
            refunds=refunds,
            total_paid=total_paid,
            expected_total=expected_total,
            total_refunded=total_refunded,
            payment_refs=payment_refs,
            evidence_refs=all_evidence_refs,
        )

        # 4. Attach inter-agent data (for canceled_order_paid rule) -----------
        result.extra["total_paid_brl"] = float(total_paid)
        result.extra["total_refunded_brl"] = float(total_refunded)
        result.extra["expected_total_brl"] = float(expected_total)
        result.extra["total_price_brl"] = float(total_price)
        result.extra["total_freight_brl"] = float(total_freight)

        # 5. Validate before returning ----------------------------------------
        errors = result.validate()
        if errors:
            # Log but don't crash — downstream verifier should catch it.
            for err in errors:
                self._trace.emit(
                    case_id=case_id,
                    event_type="verification_completed",
                    actor=ACTOR,
                    decision_code="validation_warning",
                    attributes={"warning": err},
                )

        return result

    # -------------------------------------------------------------- #
    # MCP data fetchers
    # -------------------------------------------------------------- #

    async def _fetch_payments(
        self, case_id: str, order_id: str
    ) -> tuple[list[dict], list[str], list[str]]:
        """Fetch payment records.  Returns (payments_data, payment_ref_ids, evidence_refs)."""
        try:
            evidence = await self._gw.call(
                "get_order_payments", case_id=case_id, order_id=order_id
            )
        except RuntimeError:
            return [], [], []

        ev_ref = evidence["evidence_ref"]
        self._trace.emit(
            case_id=case_id,
            event_type="tool_result_consumed",
            actor=ACTOR,
            tool_name="get_order_payments",
            evidence_refs=[ev_ref],
        )

        data = evidence.get("data", {})
        payments: list[dict] = data if isinstance(data, list) else data.get("payments", [data])

        # Build payment reference IDs for entities
        pay_refs: list[str] = []
        for idx, p in enumerate(payments):
            seq = p.get("payment_sequential", idx + 1)
            pay_refs.append(f"{order_id}_pay_{seq}")

        return payments, pay_refs, [ev_ref]

    async def _fetch_items(
        self, case_id: str, order_id: str
    ) -> tuple[list[dict], list[str]]:
        """Fetch order items.  Returns (items_data, evidence_refs)."""
        try:
            evidence = await self._gw.call(
                "get_order_items", case_id=case_id, order_id=order_id
            )
        except RuntimeError:
            return [], []

        ev_ref = evidence["evidence_ref"]
        self._trace.emit(
            case_id=case_id,
            event_type="tool_result_consumed",
            actor=ACTOR,
            tool_name="get_order_items",
            evidence_refs=[ev_ref],
        )

        data = evidence.get("data", {})
        items: list[dict] = data if isinstance(data, list) else data.get("items", [data])
        return items, [ev_ref]

    async def _fetch_refunds(
        self, case_id: str, order_id: str
    ) -> tuple[list[dict], list[str]]:
        """Fetch refund records.  Returns (refunds_data, evidence_refs)."""
        try:
            evidence = await self._gw.call(
                "get_refund_timeline", case_id=case_id, order_id=order_id
            )
        except RuntimeError:
            # Refund tool may not exist or order has no refunds
            return [], []

        ev_ref = evidence["evidence_ref"]
        self._trace.emit(
            case_id=case_id,
            event_type="tool_result_consumed",
            actor=ACTOR,
            tool_name="get_refund_timeline",
            evidence_refs=[ev_ref],
        )

        data = evidence.get("data", {})
        refunds: list[dict] = data if isinstance(data, list) else data.get("refunds", [data])
        return refunds, [ev_ref]

    # -------------------------------------------------------------- #
    # Classification logic
    # -------------------------------------------------------------- #

    def _classify(
        self,
        *,
        case_id: str,
        payments: list[dict],
        refunds: list[dict],
        total_paid: Decimal,
        expected_total: Decimal,
        total_refunded: Decimal,
        payment_refs: list[str],
        evidence_refs: list[str],
    ) -> SpecialistResult:
        """Determine the primary payment issue and build a SpecialistResult."""

        refund_lines: list[RefundLine] = []
        resolution_actions: list[str] = []

        # ---- Check for refund issues first --------------------------------
        pending_refunds = [r for r in refunds if r.get("refund_status") == "pending"]
        failed_refunds = [r for r in refunds if r.get("refund_status") == "failed"]

        if failed_refunds:
            return self._build_refund_failed_result(
                failed_refunds, payment_refs, evidence_refs
            )

        if pending_refunds:
            return self._build_refund_pending_result(
                pending_refunds, payment_refs, evidence_refs
            )

        # ---- Check for duplicate charges ----------------------------------
        duplicate = self._detect_duplicate_charge(payments)
        if duplicate is not None:
            dup_amount = _to_decimal(duplicate.get("payment_value"))
            refund_lines.append(
                RefundLine(
                    reason_code="duplicate_charge",
                    amount_brl=dup_amount,
                    entity_id=duplicate.get("payment_sequential")
                    and f"{case_id}_dup_{duplicate['payment_sequential']}",
                )
            )
            total_refund = dup_amount
            resolution_actions.append("refund_duplicate_charge")
            return SpecialistResult(
                domain="payment",
                primary_issue="duplicate_charge",
                case_status="action_required",
                confidence=0.85,
                evidence_refs=evidence_refs,
                resolution_actions=resolution_actions,
                refund_lines=refund_lines,
                recommended_refund_brl=total_refund,
                payment_references=payment_refs,
                ranked_causes=[{"cause_code": "DUPLICATE_PAYMENT", "rank": 1}],
                responsible_parties=[
                    {"party_type": "payment_provider", "party_id": None}
                ],
            )

        # ---- Check for payment mismatch -----------------------------------
        overpayment = (total_paid - expected_total).quantize(
            _TWO_PLACES, rounding=ROUND_HALF_UP
        )
        if overpayment > Decimal("0.00"):
            refund_lines.append(
                RefundLine(
                    reason_code="overpayment",
                    amount_brl=overpayment,
                    entity_id=None,
                )
            )
            resolution_actions.append("refund_overpayment")
            return SpecialistResult(
                domain="payment",
                primary_issue="payment_mismatch",
                case_status="action_required",
                confidence=0.80,
                evidence_refs=evidence_refs,
                resolution_actions=resolution_actions,
                refund_lines=refund_lines,
                recommended_refund_brl=overpayment,
                payment_references=payment_refs,
                ranked_causes=[{"cause_code": "PAYMENT_AMOUNT_MISMATCH", "rank": 1}],
                responsible_parties=[
                    {"party_type": "payment_provider", "party_id": None}
                ],
            )

        # ---- Check for underpayment (flag but don't refund) ---------------
        if overpayment < Decimal("0.00"):
            resolution_actions.append("investigate_underpayment")
            return SpecialistResult(
                domain="payment",
                primary_issue="payment_mismatch",
                case_status="needs_investigation",
                confidence=0.65,
                evidence_refs=evidence_refs,
                resolution_actions=resolution_actions,
                refund_lines=[],
                recommended_refund_brl=Decimal("0.00"),
                payment_references=payment_refs,
                ranked_causes=[{"cause_code": "PAYMENT_AMOUNT_MISMATCH", "rank": 1}],
                responsible_parties=[
                    {"party_type": "payment_provider", "party_id": None}
                ],
            )

        # ---- Valid (split) payment ----------------------------------------
        is_split = len(payments) > 1
        return SpecialistResult(
            domain="payment",
            primary_issue="valid_split_payment" if is_split else None,
            case_status="no_action",
            confidence=0.90 if is_split else 0.85,
            evidence_refs=evidence_refs,
            resolution_actions=["no_action"],
            refund_lines=[],
            recommended_refund_brl=Decimal("0.00"),
            payment_references=payment_refs,
            ranked_causes=(
                [{"cause_code": "VALID_SPLIT_PAYMENT", "rank": 1}]
                if is_split
                else []
            ),
            responsible_parties=[],
        )

    # -------------------------------------------------------------- #
    # Issue-specific builders
    # -------------------------------------------------------------- #

    @staticmethod
    def _build_refund_failed_result(
        failed_refunds: list[dict],
        payment_refs: list[str],
        evidence_refs: list[str],
    ) -> SpecialistResult:
        refund_lines = [
            RefundLine(
                reason_code="retry_failed_refund",
                amount_brl=_to_decimal(r.get("refund_amount")),
                entity_id=r.get("refund_id"),
            )
            for r in failed_refunds
        ]
        total = sum((rl.amount_brl for rl in refund_lines), start=Decimal("0.00"))
        return SpecialistResult(
            domain="payment",
            primary_issue="refund_failed",
            case_status="action_required",
            confidence=0.85,
            evidence_refs=evidence_refs,
            resolution_actions=["retry_refund", "escalate_refund_failure"],
            refund_lines=refund_lines,
            recommended_refund_brl=total,
            payment_references=payment_refs,
            ranked_causes=[{"cause_code": "REFUND_PROCESSING_FAILURE", "rank": 1}],
            responsible_parties=[
                {"party_type": "payment_provider", "party_id": None}
            ],
        )

    @staticmethod
    def _build_refund_pending_result(
        pending_refunds: list[dict],
        payment_refs: list[str],
        evidence_refs: list[str],
    ) -> SpecialistResult:
        refund_lines = [
            RefundLine(
                reason_code="pending_refund",
                amount_brl=_to_decimal(r.get("refund_amount")),
                entity_id=r.get("refund_id"),
            )
            for r in pending_refunds
        ]
        total = sum((rl.amount_brl for rl in refund_lines), start=Decimal("0.00"))
        return SpecialistResult(
            domain="payment",
            primary_issue="refund_pending",
            case_status="needs_investigation",
            confidence=0.75,
            evidence_refs=evidence_refs,
            resolution_actions=["escalate_pending_refund"],
            refund_lines=refund_lines,
            recommended_refund_brl=total,
            payment_references=payment_refs,
            ranked_causes=[{"cause_code": "REFUND_PROCESSING_DELAY", "rank": 1}],
            responsible_parties=[
                {"party_type": "payment_provider", "party_id": None}
            ],
        )

    # -------------------------------------------------------------- #
    # Duplicate detection
    # -------------------------------------------------------------- #

    @staticmethod
    def _detect_duplicate_charge(payments: list[dict]) -> dict | None:
        """Return the duplicate payment record, or ``None``."""
        if len(payments) < 2:
            return None

        seen: dict[tuple, dict] = {}
        for p in payments:
            key = (
                p.get("payment_type"),
                str(_to_decimal(p.get("payment_value"))),
                p.get("payment_installments"),
            )
            if key in seen:
                return p  # The second occurrence is the duplicate
            seen[key] = p
        return None
