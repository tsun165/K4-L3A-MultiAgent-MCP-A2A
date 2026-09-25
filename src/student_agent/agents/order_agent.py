from __future__ import annotations

from typing import Any

from ..trace import TraceWriter
from .base import EvidenceStore, SpecialistResult


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
        "get_seller",
        "get_product",
    }

    async def run(
        self,
        case: dict[str, Any],
        store: EvidenceStore,
        trace: TraceWriter,
    ) -> SpecialistResult:
        """Execute order investigation.

        TODO(Sơn): Real logic will be implemented in Task 1b.
        """
        del case, store, trace
        return SpecialistResult(actor=self.name)
