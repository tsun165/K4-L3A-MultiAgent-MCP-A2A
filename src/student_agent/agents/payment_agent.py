from __future__ import annotations

from typing import Any

from ..trace import TraceWriter
from .base import EvidenceStore, SpecialistResult


class PaymentAgent:
    """Specialist Agent for Payment, Transactions and Refunds domain.

    Owner: Đạt (liber72)
    Primary Issues:
      - valid_split_payment
      - payment_mismatch
      - duplicate_charge
      - refund_pending
      - refund_failed
    Responsible for:
      - affected_entities.payment_references
      - findings["paid_total_brl"], findings["refunded_total_brl"]
      - financial_resolution.refund_lines
    """

    name: str = "payment-agent"
    allowed_tools: set[str] = {
        "get_payment",
        "get_refund",
        "get_payment_transactions",
    }

    async def run(
        self,
        case: dict[str, Any],
        store: EvidenceStore,
        trace: TraceWriter,
    ) -> SpecialistResult:
        """Execute payment analysis and refund reconciliation.

        TODO(Đạt): Real logic will be implemented in Task 1b.
        """
        del case, store, trace
        return SpecialistResult(actor=self.name)
