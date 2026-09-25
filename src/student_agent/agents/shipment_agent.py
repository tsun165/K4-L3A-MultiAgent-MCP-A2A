"""Shipment specialist agent — analyses shipment evidence.

Responsibilities
----------------
- Fetch shipment evidence via MCP.
- Classify into: late_delivery_seller, late_delivery_logistics.
- Emit tool_result_consumed trace events for MCP calls.
"""

from __future__ import annotations

from typing import Any
from datetime import datetime

from ..mcp_gateway import EvidenceGateway
from ..trace import TraceWriter
from .specialist_result import SpecialistResult


class ShipmentAgent:
    """Specialist agent for shipment analysis.

    Usage
    -----
    >>> agent = ShipmentAgent(gateway, trace)
    >>> result = await agent.analyse(case)
    """

    def __init__(self, gateway: EvidenceGateway, trace: TraceWriter) -> None:
        self._gw = gateway
        self._trace = trace

    async def analyse(self, case: dict[str, Any]) -> SpecialistResult:
        """Run shipment analysis pipeline."""
        case_id: str = case["case_id"]
        order_id: str = case["customer_request"]["claimed_order_id"]
        
        shipment_data, shipment_ev_refs = await self._fetch_shipment(case_id, order_id)
        
        result = SpecialistResult(
            domain="shipment",
            evidence_refs=shipment_ev_refs,
        )

        if not shipment_data:
            result.primary_issue = None
            result.case_status = "needs_investigation"
            result.confidence = 0.5
            return result
            
        shipping_limit = shipment_data.get("shipping_limit_date")
        carrier_date = shipment_data.get("order_delivered_carrier_date")
        estimated_date = shipment_data.get("order_estimated_delivery_date")
        customer_date = shipment_data.get("order_delivered_customer_date")
        
        # Simplified parsing logic for ISO dates (if available)
        # We assume string comparisons work for standard ISO dates in format YYYY-MM-DD...
        
        late_to_carrier = False
        late_to_customer = False
        
        if shipping_limit and carrier_date and carrier_date > shipping_limit:
            late_to_carrier = True
            
        if estimated_date and customer_date and customer_date > estimated_date:
            late_to_customer = True
            
        # Classify
        if late_to_carrier and late_to_customer:
            result.primary_issue = "late_delivery_seller"
            result.case_status = "action_required"
            result.resolution_actions = ["contact_seller_late_shipment"]
            result.confidence = 0.85
            result.responsible_parties = [{"party_type": "seller", "party_id": "UNKNOWN"}]
        elif late_to_customer and not late_to_carrier:
            result.primary_issue = "late_delivery_logistics"
            result.case_status = "action_required"
            result.resolution_actions = ["escalate_logistics_delay"]
            result.confidence = 0.85
            result.responsible_parties = [{"party_type": "logistics_provider", "party_id": shipment_data.get("carrier", "UNKNOWN")}]
        else:
            result.primary_issue = None
            result.case_status = "needs_investigation"
            result.confidence = 0.5
            
        return result

    async def _fetch_shipment(self, case_id: str, order_id: str) -> tuple[dict[str, Any] | None, list[str]]:
        try:
            res = await self._gw.call("get_shipment_summary", case_id=case_id, order_id=order_id)
            ev_ref = res.get("evidence_ref")
            self._trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="shipment-agent",
                tool_name="get_shipment_summary",
                evidence_refs=[ev_ref] if ev_ref else []
            )
            return res.get("data", {}), [ev_ref] if ev_ref else []
        except Exception:
            return None, []
