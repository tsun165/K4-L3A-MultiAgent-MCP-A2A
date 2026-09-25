from __future__ import annotations

import asyncio
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .agents import vocab
from .agents.base import AgentMessage, EvidenceStore, SpecialistResult, send
from .agents.order_agent import ORDER_TOPICS, OrderAgent
from .agents.payment_agent import PAYMENT_TOPICS, PaymentAgent
from .agents.policy_agent import PolicyAgent
from .agents.shipment_agent import SHIPMENT_TOPICS, ShipmentAgent
from .agents.verifier import Verifier
from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter

# primary_issue -> (case_status, resolution_actions). Kept centralized so every case for
# the same issue is scored consistently (STANDARDS §6/§7). Follows EC_POLICY_V1 policy.
_ACTION_MAP: dict[str, tuple[str, list[str]]] = {
    "canceled_order_paid": ("action_required", [vocab.ACTION_ISSUE_REFUND]),
    "unavailable_order_paid": ("action_required", [vocab.ACTION_ISSUE_REFUND]),
    "late_delivery_seller": ("action_required", [vocab.ACTION_REFUND_FREIGHT]),
    "late_delivery_logistics": ("action_required", [vocab.ACTION_REFUND_FREIGHT]),
    "valid_split_payment": ("no_action", [vocab.ACTION_DOCUMENT_NO_ACTION]),
    "payment_mismatch": ("action_required", [vocab.ACTION_ISSUE_REFUND]),
    "duplicate_charge": ("action_required", [vocab.ACTION_ISSUE_REFUND]),
    "refund_pending": ("action_required", [vocab.ACTION_MONITOR_REFUND]),
    "refund_failed": ("action_required", [vocab.ACTION_RETRY_REFUND]),
    "unsupported_claim": ("no_action", [vocab.ACTION_DOCUMENT_NO_ACTION]),
    "insufficient_evidence": ("needs_investigation", [vocab.ACTION_REQUEST_MANUAL_REVIEW]),
}
_ALL_CLAIM_TOPICS = ORDER_TOPICS | SHIPMENT_TOPICS | PAYMENT_TOPICS

_RELEVANT_DOMAINS_BY_ISSUE: dict[str, set[str]] = {
    "canceled_order_paid": {"order", "item", "payment"},
    "unavailable_order_paid": {"order", "item", "payment", "seller"},
    "late_delivery_seller": {"shipment", "order", "item", "seller"},
    "late_delivery_logistics": {"shipment", "order", "item"},
    "valid_split_payment": {"payment", "order", "item"},
    "payment_mismatch": {"payment", "order", "item"},
    "duplicate_charge": {"payment", "order"},
    "refund_pending": {"refund", "payment", "order"},
    "refund_failed": {"refund", "payment", "order"},
    "unsupported_claim": {"order", "item", "shipment", "payment"},
    "insufficient_evidence": {"order", "item", "shipment", "payment", "refund", "seller"},
}


def _filter_relevant_evidence(
    primary_issue: str, candidate_refs: list[str], store: EvidenceStore
) -> list[str]:
    """Filter evidence refs to strictly include domains relevant to the primary issue."""
    allowed_domains = _RELEVANT_DOMAINS_BY_ISSUE.get(primary_issue)
    if not allowed_domains:
        return list(dict.fromkeys(candidate_refs))[:30]

    filtered: list[str] = []
    for ref in candidate_refs:
        rec = store._records.get(ref)
        if rec is not None:
            if rec.domain in allowed_domains:
                filtered.append(ref)
        else:
            filtered.append(ref)

    if not filtered:
        filtered = candidate_refs
    return list(dict.fromkeys(filtered))[:30]



async def solve_case(
    case: dict[str, Any],
    gateway: EvidenceGateway,
    trace: TraceWriter,
) -> dict[str, Any]:
    """Coordinator + specialist-agent workflow for one L3A case.

    Lifecycle (CLI emits case_received before and case_finalized after this call):
      task_assigned (coordinator -> each specialist)
      -> tool_result_consumed (per EvidenceStore.fetch)
      -> handoff (specialist -> coordinator)
      -> handoff (coordinator -> policy-agent) -> policy_decided
      -> handoff (coordinator -> verifier) -> verification_completed
    """
    case_id: str = case["case_id"]
    store = EvidenceStore(case_id=case_id, gateway=gateway, trace=trace)

    order_agent = OrderAgent()
    shipment_agent = ShipmentAgent()
    payment_agent = PaymentAgent()
    policy_agent = PolicyAgent()
    verifier = Verifier()

    for specialist in (order_agent, shipment_agent, payment_agent, policy_agent):
        store.register_actor_tools(specialist.name, specialist.allowed_tools)

    domain_specialists = [order_agent, shipment_agent, payment_agent]
    for specialist in domain_specialists:
        trace.emit(
            case_id=case_id,
            event_type="task_assigned",
            actor="coordinator",
            target=specialist.name,
            decision_code="investigate_domain",
        )

    async def _safe_run(specialist: Any) -> SpecialistResult:
        try:
            return await specialist.run(case, store, trace)
        except Exception as exc:  # noqa: BLE001 - specialists must never crash a case
            return SpecialistResult(actor=specialist.name, errors=[f"{type(exc).__name__}: {exc}"])

    order_res, shipment_res, payment_res = await asyncio.gather(
        _safe_run(order_agent), _safe_run(shipment_agent), _safe_run(payment_agent)
    )
    specialist_results = [order_res, shipment_res, payment_res]

    # Bridge: payment totals finish the canceled/unavailable-order refund (STANDARDS §6/§8).
    order_agent.apply_payment_totals(order_res, payment_res)

    for res in specialist_results:
        send(
            AgentMessage(
                case_id=case_id,
                sender=res.actor,
                recipient="coordinator",
                task=f"{res.actor}_completed",
                payload={"candidate_issues": res.candidate_issues},
                evidence_refs=res.evidence_refs,
            ),
            trace,
        )

    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target=policy_agent.name,
        decision_code="fetch_policy",
    )
    policy_res = await _safe_run(policy_agent)
    send(
        AgentMessage(
            case_id=case_id,
            sender=policy_agent.name,
            recipient="coordinator",
            task="policy_fetch_completed",
            evidence_refs=policy_res.evidence_refs,
        ),
        trace,
    )

    claimed_topics = {c.get("topic") for c in case["customer_request"].get("claims", [])}
    primary_issue, top_score = _resolve_primary_issue(specialist_results, claimed_topics)
    case_status, resolution_actions = _ACTION_MAP.get(
        primary_issue, ("needs_investigation", [vocab.ACTION_REQUEST_MANUAL_REVIEW])
    )

    # Dynamic adjustment for payment_mismatch based on diff
    if primary_issue == "payment_mismatch":
        diff_brl = payment_res.findings.get("diff_brl", 0.0)
        if diff_brl <= 0:
            case_status = "no_action"
            resolution_actions = [vocab.ACTION_DOCUMENT_NO_ACTION]
        else:
            case_status = "action_required"
            resolution_actions = [vocab.ACTION_ISSUE_REFUND]

    policy_decision_code = {
        "action_required": vocab.POLICY_REFUND_ELIGIBLE,
        "no_action": vocab.POLICY_REFUND_NOT_ELIGIBLE,
        "needs_investigation": vocab.POLICY_NEEDS_REVIEW,
    }[case_status]
    trace.emit(
        case_id=case_id,
        event_type="policy_decided",
        actor=policy_agent.name,
        decision_code=policy_decision_code,
        evidence_refs=policy_res.evidence_refs or None,
    )

    merged_entities = _merge_entities(specialist_results)
    ranked_causes = _merge_causes(specialist_results, primary_issue)
    responsible_parties = _merge_parties(specialist_results, primary_issue, merged_entities)
    refund_lines, total_refund = _merge_refunds(specialist_results, case_status)
    all_refs = list(dict.fromkeys(ref for res in specialist_results for ref in res.evidence_refs))
    supporting_refs = _filter_relevant_evidence(primary_issue, all_refs, store)
    claim_assessments = _build_claim_assessments(
        case, specialist_results, primary_issue, top_score, supporting_refs
    )

    candidate_output: dict[str, Any] = {
        "schema_version": "day09-l3a-output-v2",
        "case_id": case_id,
        "assessment": {
            "primary_issue": primary_issue,
            "case_status": case_status,
            "confidence": float(round(top_score, 2)),
        },
        "affected_entities": merged_entities,
        "claim_assessments": claim_assessments,
        "root_cause_analysis": {
            "ranked_causes": ranked_causes,
            "responsible_parties": responsible_parties[:5],
        },
        "evidence_refs": supporting_refs,
        "data_conflicts": [],
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": float(total_refund),
            "refund_lines": refund_lines,
        },
        "resolution_actions": resolution_actions,
    }

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
    return verifier.verify_and_repair(candidate_output, store, trace)


def _resolve_primary_issue(
    results: list[SpecialistResult], claimed_topics: set[str]
) -> tuple[str, float]:
    candidates = [c for r in results for c in r.candidate_issues]
    if candidates:
        candidates.sort(key=lambda c: c[1], reverse=True)
        top_issue, top_score = candidates[0]
        runner_up_differs = len(candidates) > 1 and candidates[1][0] != top_issue
        is_tie = runner_up_differs and candidates[1][1] == top_score
        return ("insufficient_evidence", 0.25) if is_tie else (top_issue, top_score)

    investigated_topics: set[str] = set()
    contradicted_topics: set[str] = set()
    for r in results:
        for topic, verdict in r.findings.get("claim_check", {}).items():
            investigated_topics.add(topic)
            if verdict == vocab.CLAIM_CONTRADICTED:
                contradicted_topics.add(topic)

    primary_claimed = (claimed_topics & _ALL_CLAIM_TOPICS) or set()
    fully_investigated = investigated_topics >= primary_claimed
    fully_contradicted = contradicted_topics >= primary_claimed
    if primary_claimed and fully_investigated and fully_contradicted:
        return "unsupported_claim", 0.8

    # The customer's own claim can literally be topic "unsupported_claim" (a vague
    # complaint with no specific, checkable assertion -- observed live, e.g. case 010's
    # message "giao nhận không đúng cam kết" names no domain fact to verify). No
    # specialist owns that literal string as an investigation target, so
    # `primary_claimed` above is empty and the case would otherwise fall through to
    # insufficient_evidence even though nothing is actually missing.
    if "unsupported_claim" in claimed_topics and not primary_claimed:
        return "unsupported_claim", 0.85

    return "insufficient_evidence", 0.25


def _merge_entities(results: list[SpecialistResult]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {
        "order_ids": [],
        "item_ids": [],
        "seller_ids": [],
        "payment_references": [],
        "shipment_ids": [],
    }
    for res in results:
        for key in merged:
            merged[key].extend(res.entities.get(key, []))
    return {key: list(dict.fromkeys(values))[:20] for key, values in merged.items()}


def _merge_causes(results: list[SpecialistResult], primary_issue: str) -> list[dict[str, Any]]:
    codes = list(dict.fromkeys(code for res in results for code in res.cause_codes))[:5]
    if not codes:
        fallback = {
            "unsupported_claim": vocab.CLAIM_NOT_SUPPORTED_BY_EVIDENCE,
            "insufficient_evidence": vocab.EVIDENCE_UNAVAILABLE,
        }.get(primary_issue, vocab.EVIDENCE_UNAVAILABLE)
        codes = [fallback]
    return [{"cause_code": code, "rank": idx + 1} for idx, code in enumerate(codes)]


def _merge_parties(
    results: list[SpecialistResult], primary_issue: str, entities: dict[str, list[str]]
) -> list[dict[str, Any]]:
    parties = [p for res in results for p in res.responsible_parties]
    seen: set[tuple[str, str | None]] = set()
    unique: list[dict[str, Any]] = []
    for p in parties:
        key = (p.get("party_type", "unknown"), p.get("party_id"))
        if key not in seen:
            seen.add(key)
            unique.append(p)
    if unique:
        return unique
    if primary_issue in {"unsupported_claim", "insufficient_evidence"}:
        return [{"party_type": "unknown", "party_id": None}]
    return [{"party_type": "unknown", "party_id": None}]


def _merge_refunds(
    results: list[SpecialistResult], case_status: str
) -> tuple[list[dict[str, Any]], Decimal]:
    if case_status == "no_action":
        return [], Decimal("0.00")
    lines: list[dict[str, Any]] = []
    total = Decimal("0.00")
    for res in results:
        for line in res.refund_lines:
            try:
                amt = Decimal(str(line.get("amount_brl", 0))).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            except Exception:  # noqa: BLE001
                continue
            if amt <= 0:
                continue
            lines.append(
                {
                    "reason_code": str(line.get("reason_code", "REFUND_REQUESTED")),
                    "amount_brl": float(amt),
                    "entity_id": line.get("entity_id"),
                }
            )
            total += amt
    return lines[:10], total


def _build_claim_assessments(
    case: dict[str, Any],
    results: list[SpecialistResult],
    primary_issue: str,
    confidence: float,
    supporting_refs: list[str],
) -> list[dict[str, Any]]:
    claim_check_by_topic: dict[str, str] = {}
    refs_by_topic: dict[str, list[str]] = {}
    for res in results:
        for topic, verdict in res.findings.get("claim_check", {}).items():
            claim_check_by_topic[topic] = verdict
            refs_by_topic[topic] = res.evidence_refs

    assessments: list[dict[str, Any]] = []
    for claim in case["customer_request"].get("claims", [])[:5]:
        claim_id = claim.get("claim_id", "unknown-claim")
        topic = claim.get("topic", "")
        verdict_code = claim_check_by_topic.get(topic)
        refs = refs_by_topic.get(topic, [])
        if verdict_code == vocab.CLAIM_SUPPORTED or topic == primary_issue:
            verdict, conf = "supported", confidence
        elif verdict_code == vocab.CLAIM_CONTRADICTED:
            verdict, conf = "unsupported", 0.85
        elif primary_issue == "insufficient_evidence":
            verdict, conf = "insufficient_evidence", 0.25
        else:
            verdict, conf = "unsupported", 0.7

        final_refs = list(dict.fromkeys(refs))
        if not final_refs:
            final_refs = supporting_refs

        assessments.append(
            {
                "claim_id": claim_id,
                "verdict": verdict,
                "confidence": float(round(conf, 2)),
                "evidence_refs": final_refs[:30],
            }
        )
    return assessments
