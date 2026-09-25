from __future__ import annotations

from typing import Any

from ..trace import TraceWriter
from .base import EvidenceStore, SpecialistResult


class ShipmentAgent:
    """Specialist Agent for Shipment and Delivery domain.

    Owner: Sơn (tsun165)
    Primary Issues:
      - late_delivery_seller
      - late_delivery_logistics
    Responsible for affected_entities:
      - shipment_ids
    """

    name: str = "shipment-agent"
    allowed_tools: set[str] = {
        "get_shipment",
        "get_delivery_timeline",
    }

    async def run(
        self,
        case: dict[str, Any],
        store: EvidenceStore,
        trace: TraceWriter,
    ) -> SpecialistResult:
        """Execute shipment timeline and carrier delay investigation.

        TODO(Sơn): Real logic will be implemented in Task 1b.
        """
        del case, store, trace
        return SpecialistResult(actor=self.name)
