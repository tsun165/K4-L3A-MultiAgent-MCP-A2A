from __future__ import annotations

import asyncio
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .agents.base import AgentMessage, EvidenceStore, SpecialistResult, send
from .agents.order_agent import OrderAgent
from .agents.payment_agent import PaymentAgent
from .agents.policy_agent import PolicyAgent
from .agents.shipment_agent import ShipmentAgent
from .agents.verifier import Verifier
from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter


async def solve_case(
    case: dict[str, Any],
    gateway: EvidenceGateway,
    trace: TraceWriter,
) -> dict[str, Any]:
    """Execute the multi-agent investigation workflow for a single customer dispute case.

    Lifecycle:
      (CLI: case_received)
      -> Coordinator assigns tasks (trace: task_assigned)
      -> Specialists investigate domain via MCP Gateway (trace: tool_result_consumed)
      -> Specialists send results back to coordinator (trace: handoff)
      -> Coordinator consults Policy Agent (trace: task_assigned, handoff, policy_decided)
      -> Coordinator synthesizes candidate output
      -> Coordinator hands off to Verifier (trace: handoff)
      -> Verifier validates invariants & auto-repairs (trace: verification_completed)
      -> (CLI: case_finalized)
    """
    case_id: str = case["case_id"]

    # 1. Instantiate per-case isolated EvidenceStore
    store = EvidenceStore(case_id=case_id, gateway=gateway, trace=trace)

    # 2. Instantiate specialists
    order_agent = OrderAgent()
    shipment_agent = ShipmentAgent()
    payment_agent = PaymentAgent()
    policy_agent = PolicyAgent()
    verifier = Verifier()

    # Register allowed tools for strict authorization
    store.register_actor_tools(order_agent.name, order_agent.allowed_tools)
    store.register_actor_tools(shipment_agent.name, shipment_agent.allowed_tools)
    store.register_actor_tools(payment_agent.name, payment_agent.allowed_tools)
    store.register_actor_tools(policy_agent.name, policy_agent.allowed_tools)

    # 3. Coordinator assigns tasks to domain specialists
    domain_specialists = [order_agent, shipment_agent, payment_agent]
    for specialist in domain_specialists:
        trace.emit(
            case_id=case_id,
            event_type="task_assigned",
            actor="coordinator",
            target=specialist.name,
            decision_code="investigate_domain",
        )

    # 4. Execute domain specialists concurrently with fault isolation
    async def _safe_run(specialist: Any) -> SpecialistResult:
        try:
            return await specialist.run(case, store, trace)
        except Exception as exc:
            return SpecialistResult(actor=specialist.name, errors=[str(exc)])

    specialist_results: list[SpecialistResult] = await asyncio.gather(
        _safe_run(order_agent),
        _safe_run(shipment_agent),
        _safe_run(payment_agent),
    )

    # 5. Handoff results from each specialist back to coordinator
    for res in specialist_results:
        msg = AgentMessage(
            case_id=case_id,
            sender=res.actor,
            recipient="coordinator",
            task=f"{res.actor}_completed",
            payload={"candidate_issues": res.candidate_issues, "findings": res.findings},
            evidence_refs=res.evidence_refs,
        )
        send(msg, trace)

    order_res, shipment_res, payment_res = specialist_results

    # 6. Policy evaluation
    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target=policy_agent.name,
        decision_code="evaluate_policy",
    )

    policy_res = await _safe_run(policy_agent)

    send(
        AgentMessage(
            case_id=case_id,
            sender=policy_agent.name,
            recipient="coordinator",
            task="policy_evaluation_completed",
            payload=policy_res.findings,
            evidence_refs=policy_res.evidence_refs,
        ),
        trace,
    )

    policy_decision_code = policy_res.findings.get(
        "policy_decision_code", "STANDARD_POLICY_APPLIED"
    )
    trace.emit(
        case_id=case_id,
        event_type="policy_decided",
        actor=policy_agent.name,
        decision_code=policy_decision_code,
    )

    # 7. Synthesize Candidate Output
    all_candidates: list[tuple[str, float]] = []
    for res in specialist_results:
        all_candidates.extend(res.candidate_issues)

    # Pick primary issue
    if all_candidates:
        all_candidates.sort(key=lambda item: item[1], reverse=True)
        top_issue, top_score = all_candidates[0]
        # Check for tie
        is_tie = (
            len(all_candidates) > 1
            and all_candidates[1][1] == top_score
            and all_candidates[1][0] != top_issue
        )
        if is_tie:
            primary_issue = "insufficient_evidence"
            confidence = 0.25
        else:
            primary_issue = top_issue
            confidence = top_score
    else:
        primary_issue = "insufficient_evidence"
        confidence = 0.25

    # Determine default case status
    if "case_status" in policy_res.findings:
        case_status = policy_res.findings["case_status"]
    elif primary_issue in ["valid_split_payment", "unsupported_claim"]:
        case_status = "no_action"
    elif primary_issue in [
        "canceled_order_paid",
        "unavailable_order_paid",
        "late_delivery_seller",
        "late_delivery_logistics",
        "payment_mismatch",
        "duplicate_charge",
        "refund_failed",
    ]:
        case_status = "action_required"
    else:
        case_status = "needs_investigation"

    # Merge affected entities
    merged_entities: dict[str, list[str]] = {
        "order_ids": [],
        "item_ids": [],
        "seller_ids": [],
        "payment_references": [],
        "shipment_ids": [],
    }
    for res in [*specialist_results, policy_res]:
        for k in merged_entities:
            merged_entities[k].extend(res.entities.get(k, []))

    for k in merged_entities:
        merged_entities[k] = list(dict.fromkeys(merged_entities[k]))[:20]

    # Combine root cause codes
    all_cause_codes: list[str] = []
    for res in specialist_results:
        all_cause_codes.extend(res.cause_codes)

    ranked_causes: list[dict[str, Any]] = []
    if all_cause_codes:
        unique_codes = list(dict.fromkeys(all_cause_codes))[:5]
        ranked_causes = [
            {"cause_code": code.upper().replace("-", "_"), "rank": idx + 1}
            for idx, code in enumerate(unique_codes)
        ]
    else:
        ranked_causes = [{"cause_code": primary_issue.upper().replace("-", "_"), "rank": 1}]

    # Combine responsible parties
    combined_parties: list[dict[str, Any]] = []
    for res in specialist_results:
        combined_parties.extend(res.responsible_parties)

    if not combined_parties:
        if "seller" in primary_issue and merged_entities["seller_ids"]:
            seller_id = merged_entities["seller_ids"][0]
            combined_parties = [{"party_type": "seller", "party_id": seller_id}]
        elif "logistics" in primary_issue:
            combined_parties = [{"party_type": "logistics_provider", "party_id": None}]
        elif "payment" in primary_issue or "charge" in primary_issue:
            combined_parties = [{"party_type": "payment_provider", "party_id": None}]
        else:
            combined_parties = [{"party_type": "unknown", "party_id": None}]

    # Combine financial resolution
    combined_refund_lines: list[dict[str, Any]] = []
    for res in [*specialist_results, policy_res]:
        combined_refund_lines.extend(res.refund_lines)

    total_refund = Decimal("0.00")
    cleaned_refund_lines: list[dict[str, Any]] = []
    if case_status != "no_action":
        for line in combined_refund_lines[:10]:
            try:
                amt = Decimal(str(line.get("amount_brl", 0))).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                if amt > 0:
                    cleaned_refund_lines.append(
                        {
                            "reason_code": str(line.get("reason_code", "REFUND_REQUESTED")),
                            "amount_brl": float(amt),
                            "entity_id": line.get("entity_id"),
                        }
                    )
                    total_refund += amt
            except Exception:
                continue

    # Resolution actions
    resolution_actions: list[str] = []
    if "resolution_actions" in policy_res.findings:
        resolution_actions.extend(policy_res.findings["resolution_actions"])
    else:
        if case_status == "no_action":
            resolution_actions = ["no_action_required"]
        elif case_status == "action_required":
            resolution_actions = ["process_claim_resolution"]
        else:
            resolution_actions = ["manual_investigation_required"]

    # Claim assessments
    customer_claims = case.get("customer_request", {}).get("claims", [])
    claim_assessments: list[dict[str, Any]] = []
    for claim in customer_claims[:5]:
        cid = claim.get("claim_id", "unknown-claim")
        topic = claim.get("topic", "")
        if topic == primary_issue:
            verdict = "supported"
            c_conf = confidence
        elif primary_issue == "insufficient_evidence":
            verdict = "insufficient_evidence"
            c_conf = 0.25
        else:
            verdict = "unsupported"
            c_conf = 0.85
        claim_assessments.append(
            {
                "claim_id": cid,
                "verdict": verdict,
                "confidence": float(round(c_conf, 2)),
                "evidence_refs": [],
            }
        )

    # Evidence refs used
    supporting_refs: list[str] = []
    for res in specialist_results:
        supporting_refs.extend(res.evidence_refs)
    supporting_refs = list(dict.fromkeys(supporting_refs))[:30]

    candidate_output: dict[str, Any] = {
        "schema_version": "day09-l3a-output-v2",
        "case_id": case_id,
        "assessment": {
            "primary_issue": primary_issue,
            "case_status": case_status,
            "confidence": float(round(confidence, 2)),
        },
        "affected_entities": merged_entities,
        "claim_assessments": claim_assessments,
        "root_cause_analysis": {
            "ranked_causes": ranked_causes,
            "responsible_parties": combined_parties[:5],
        },
        "evidence_refs": supporting_refs,
        "data_conflicts": [],
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": float(total_refund),
            "refund_lines": cleaned_refund_lines,
        },
        "resolution_actions": resolution_actions,
    }

    # 8. Handoff to Verifier
    send(
        AgentMessage(
            case_id=case_id,
            sender="coordinator",
            recipient=verifier.name,
            task="verify_case_output",
            evidence_refs=supporting_refs,
        ),
        trace,
    )

    # 9. Verifier audits, repairs and emits verification_completed trace event
    final_output = verifier.verify_and_repair(candidate_output, store, trace)
    return final_output
