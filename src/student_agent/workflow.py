from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter
from .contracts import Contracts
from .agents import PaymentAgent, PolicyAgent, OrderAgent, ShipmentAgent, Verifier


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    """Implement the L3A coordinator and specialist-agent workflow here."""
    
    case_id = case["case_id"]
    
    # Instantiate agents
    payment_agent = PaymentAgent(gateway, trace)
    policy_agent = PolicyAgent(gateway, trace)
    order_agent = OrderAgent(gateway, trace)
    shipment_agent = ShipmentAgent(gateway, trace)
    
    # Emit task assigned
    trace.emit(case_id=case_id, event_type="task_assigned", actor="coordinator", target="payment-agent")
    payment_result = await payment_agent.analyse(case)
    
    trace.emit(case_id=case_id, event_type="task_assigned", actor="coordinator", target="order-agent")
    order_result = await order_agent.analyse(case, payment_result.extra)
    
    trace.emit(case_id=case_id, event_type="task_assigned", actor="coordinator", target="shipment-agent")
    shipment_result = await shipment_agent.analyse(case)
    
    trace.emit(case_id=case_id, event_type="task_assigned", actor="coordinator", target="policy-agent")
    policy_result = await policy_agent.analyse(case)
    
    # Handoff events
    for actor in ["payment-agent", "order-agent", "shipment-agent", "policy-agent"]:
        trace.emit(case_id=case_id, event_type="handoff", actor=actor, target="coordinator")
        
    # Merge results
    results = [payment_result, order_result, shipment_result, policy_result]
    
    all_refs = []
    for r in results:
        if r:
            all_refs.extend(r.evidence_refs)
            
    # Remove duplicates but preserve order
    seen = set()
    unique_refs = []
    for ref in all_refs:
        if ref not in seen:
            seen.add(ref)
            unique_refs.append(ref)
            
    # Find the issue with highest confidence
    best_result = None
    best_conf = -1.0
    for r in results:
        if r and r.primary_issue and r.confidence > best_conf:
            best_conf = r.confidence
            best_result = r
            
    # Build output
    if best_result:
        primary_issue = best_result.primary_issue
        case_status = best_result.case_status
        confidence = best_result.confidence
        resolution_actions = list(set(best_result.resolution_actions))
        refund_lines = best_result.refund_lines
        recommended = best_result.recommended_refund_brl
    else:
        primary_issue = "insufficient_evidence"
        case_status = "needs_investigation"
        confidence = 0.5
        resolution_actions = []
        refund_lines = []
        recommended = Decimal("0.00")
        
    output = {
        "schema_version": "day09-l3a-output-v2",
        "case_id": case_id,
        "primary_issue": primary_issue,
        "case_status": case_status,
        "confidence": confidence,
        "evidence_refs": unique_refs[:30], # max 30
        "resolution_actions": resolution_actions,
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": float(recommended.quantize(Decimal("0.01"))),
            "refund_lines": [line.to_dict() for line in refund_lines]
        },
        "affected_entities": {
            "order_ids": [case.get("customer_request", {}).get("claimed_order_id")] if case.get("customer_request", {}).get("claimed_order_id") else [],
            "item_ids": [],
            "seller_ids": [],
            "payment_references": payment_result.payment_references if payment_result else [],
            "shipment_ids": []
        },
        "root_cause_analysis": {
            "ranked_causes": best_result.ranked_causes if best_result else [],
            "responsible_parties": best_result.responsible_parties if best_result else [],
            "data_conflicts": []
        },
        "claim_assessments": []
    }
    
    # Verification
    trace.emit(case_id=case_id, event_type="handoff", actor="coordinator", target="verifier")
    contracts = trace.contracts
    verifier = Verifier(contracts)
    
    output, is_valid, errors = verifier.verify_and_repair(output, case_id)
    
    trace.emit(
        case_id=case_id,
        event_type="verification_completed",
        actor="verifier",
        decision_code="PASS" if is_valid else "REPAIRED",
        attributes={"error_count": len(errors)}
    )
    
    return output
