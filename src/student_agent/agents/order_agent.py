"""Order specialist agent — analyses order and item evidence.

Responsibilities
----------------
- Fetch order and item evidence via MCP.
- Classify into: canceled_order_paid, unavailable_order_paid.
- Use total paid/refunded provided by the coordinator from payment agent.
- Emit tool_result_consumed trace events for MCP calls.
"""

from __future__ import annotations

from typing import Any

from ..mcp_gateway import EvidenceGateway
from ..trace import TraceWriter
from .specialist_result import SpecialistResult


class OrderAgent:
    """Specialist agent for order analysis.

    Usage
    -----
    >>> agent = OrderAgent(gateway, trace)
    >>> result = await agent.analyse(case, extra_data)
    """

    def __init__(self, gateway: EvidenceGateway, trace: TraceWriter) -> None:
        self._gw = gateway
        self._trace = trace

    async def analyse(self, case: dict[str, Any], extra: dict[str, Any]) -> SpecialistResult:
        """Run order analysis pipeline."""
        case_id: str = case["case_id"]
        order_id: str = case["customer_request"]["claimed_order_id"]
        
        # 1. Fetch order
        order_data, order_ev_refs = await self._fetch_order(case_id, order_id)
        
        # 2. Fetch items
        items_data, items_ev_refs = await self._fetch_items(case_id, order_id)
        
        all_ev_refs = order_ev_refs + items_ev_refs
        
        order_status = order_data.get("order_status", "") if order_data else ""
        
        total_paid_brl = extra.get("total_paid_brl", 0)
        total_refunded_brl = extra.get("total_refunded_brl", 0)
        net_paid = total_paid_brl - total_refunded_brl

        result = SpecialistResult(
            domain="order",
            evidence_refs=all_ev_refs,
        )

        # Classify
        if order_status == "canceled" and net_paid > 0:
            result.primary_issue = "canceled_order_paid"
            result.case_status = "action_required"
            result.resolution_actions = ["refund_canceled_order"]
            result.confidence = 0.95
        elif order_status == "unavailable" and net_paid > 0:
            result.primary_issue = "unavailable_order_paid"
            result.case_status = "action_required"
            result.resolution_actions = ["refund_unavailable_order"]
            result.confidence = 0.95
        else:
            result.primary_issue = None
            result.case_status = "needs_investigation"
            result.confidence = 0.5
            
        return result

    async def _fetch_order(self, case_id: str, order_id: str) -> tuple[dict[str, Any] | None, list[str]]:
        try:
            res = await self._gw.call("get_order", case_id=case_id, order_id=order_id)
            ev_ref = res.get("evidence_ref")
            self._trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="order-agent",
                tool_name="get_order",
                evidence_refs=[ev_ref] if ev_ref else []
            )
            return res.get("data", {}), [ev_ref] if ev_ref else []
        except Exception:
            return None, []

    async def _fetch_items(self, case_id: str, order_id: str) -> tuple[list[dict[str, Any]], list[str]]:
        try:
            res = await self._gw.call("get_order_items", case_id=case_id, order_id=order_id)
            ev_ref = res.get("evidence_ref")
            self._trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="order-agent",
                tool_name="get_order_items",
                evidence_refs=[ev_ref] if ev_ref else []
            )
            return res.get("data", []), [ev_ref] if ev_ref else []
        except Exception:
            return [], []
