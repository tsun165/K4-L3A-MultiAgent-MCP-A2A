"""Policy specialist agent — reads policy via MCP and emits ``policy_decided``.

Responsibilities
----------------
- Fetch the applicable e-commerce policy via MCP for the case.
- Interpret policy rules and determine the customer's entitlement.
- Emit ``policy_decided`` trace event with the decision code.
- Return a ``SpecialistResult`` with policy-derived guidance that the
  coordinator uses to cross-check or override specialist recommendations.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..mcp_gateway import EvidenceGateway
from ..trace import TraceWriter
from .specialist_result import SpecialistResult

# ------------------------------------------------------------------ #
# Constants
# ------------------------------------------------------------------ #

ACTOR = "policy-agent"

# Mapping from claim topics to expected policy decision codes
_TOPIC_TO_DECISION: dict[str, str] = {
    "canceled_order_paid": "FULL_REFUND_ENTITLED",
    "unavailable_order_paid": "FULL_REFUND_ENTITLED",
    "late_delivery_seller": "PARTIAL_REFUND_OR_RESHIP",
    "late_delivery_logistics": "PARTIAL_REFUND_OR_RESHIP",
    "valid_split_payment": "NO_ACTION_NEEDED",
    "payment_mismatch": "REFUND_OVERPAYMENT",
    "duplicate_charge": "REFUND_DUPLICATE",
    "refund_pending": "MONITOR_REFUND",
    "refund_failed": "RETRY_OR_ESCALATE",
    "requested_full_refund": "EVALUATE_REFUND_ELIGIBILITY",
}


# ------------------------------------------------------------------ #
# PolicyAgent
# ------------------------------------------------------------------ #


class PolicyAgent:
    """Specialist agent that reads e-commerce policy and decides entitlements.

    Usage
    -----
    >>> agent = PolicyAgent(gateway, trace)
    >>> result = await agent.analyse(case)
    """

    def __init__(self, gateway: EvidenceGateway, trace: TraceWriter) -> None:
        self._gw = gateway
        self._trace = trace

    # -------------------------------------------------------------- #
    # Public API
    # -------------------------------------------------------------- #

    async def analyse(self, case: dict[str, Any]) -> SpecialistResult:
        """Read policy via MCP and return a ``SpecialistResult`` with the decision."""
        case_id: str = case["case_id"]
        policy_version: str = case.get("policy_version", "EC_POLICY_V1")
        claims: list[dict] = case.get("customer_request", {}).get("claims", [])

        # 1. Fetch policy evidence -------------------------------------------
        policy_data, policy_ev_refs = await self._fetch_policy(
            case_id, policy_version
        )

        # 2. Determine topics from claims ------------------------------------
        topics = [c.get("topic", "") for c in claims]
        primary_topic = topics[0] if topics else "unsupported_claim"

        # 3. Decide based on policy ------------------------------------------
        decision_code = self._decide(primary_topic, policy_data)

        # 4. Emit policy_decided trace event ---------------------------------
        self._trace.emit(
            case_id=case_id,
            event_type="policy_decided",
            actor=ACTOR,
            decision_code=decision_code,
            evidence_refs=policy_ev_refs,
            attributes={
                "policy_version": policy_version,
                "primary_topic": primary_topic,
            },
        )

        # 5. Build result -----------------------------------------------------
        case_status = self._map_status(decision_code)
        resolution_actions = self._map_actions(decision_code, primary_topic)

        return SpecialistResult(
            domain="policy",
            primary_issue=primary_topic if primary_topic in _TOPIC_TO_DECISION else None,
            case_status=case_status,
            confidence=0.80 if policy_data else 0.50,
            evidence_refs=policy_ev_refs,
            resolution_actions=resolution_actions,
            refund_lines=[],
            recommended_refund_brl=Decimal("0.00"),
            payment_references=[],
            ranked_causes=self._build_causes(decision_code),
            responsible_parties=self._build_parties(primary_topic),
            extra={
                "decision_code": decision_code,
                "policy_version": policy_version,
                "topics": topics,
            },
        )

    # -------------------------------------------------------------- #
    # MCP data fetchers
    # -------------------------------------------------------------- #

    async def _fetch_policy(
        self, case_id: str, policy_version: str
    ) -> tuple[dict[str, Any], list[str]]:
        """Fetch e-commerce policy.  Returns (policy_data, evidence_refs)."""
        try:
            evidence = await self._gw.call(
                "get_policy",
                case_id=case_id,
                policy_version=policy_version,
            )
        except RuntimeError:
            return {}, []

        ev_ref = evidence["evidence_ref"]
        self._trace.emit(
            case_id=case_id,
            event_type="tool_result_consumed",
            actor=ACTOR,
            tool_name="get_policy",
            evidence_refs=[ev_ref],
        )

        return evidence.get("data", {}), [ev_ref]

    # -------------------------------------------------------------- #
    # Decision logic
    # -------------------------------------------------------------- #

    @staticmethod
    def _decide(primary_topic: str, policy_data: dict[str, Any]) -> str:
        """Return a decision code based on topic and policy rules.

        If the policy_data contains specific rules, they take precedence.
        Otherwise fall back to the default topic-to-decision mapping.
        """
        # If the server returned explicit entitlement rules, use them
        rules = policy_data.get("rules", [])
        if isinstance(rules, list):
            for rule in rules:
                if isinstance(rule, dict) and rule.get("topic") == primary_topic:
                    return rule.get("decision", _TOPIC_TO_DECISION.get(
                        primary_topic, "NEEDS_REVIEW"
                    ))

        # Fallback to default mapping
        return _TOPIC_TO_DECISION.get(primary_topic, "NEEDS_REVIEW")

    @staticmethod
    def _map_status(decision_code: str) -> str:
        """Map a policy decision code to a case_status."""
        no_action_decisions = {"NO_ACTION_NEEDED"}
        investigate_decisions = {"MONITOR_REFUND", "NEEDS_REVIEW", "EVALUATE_REFUND_ELIGIBILITY"}

        if decision_code in no_action_decisions:
            return "no_action"
        if decision_code in investigate_decisions:
            return "needs_investigation"
        return "action_required"

    @staticmethod
    def _map_actions(decision_code: str, primary_topic: str) -> list[str]:
        """Derive resolution actions from the decision code."""
        action_map: dict[str, list[str]] = {
            "FULL_REFUND_ENTITLED": [f"refund_{primary_topic}"],
            "PARTIAL_REFUND_OR_RESHIP": [f"evaluate_{primary_topic}_compensation"],
            "REFUND_OVERPAYMENT": ["refund_overpayment"],
            "REFUND_DUPLICATE": ["refund_duplicate_charge"],
            "MONITOR_REFUND": ["escalate_pending_refund"],
            "RETRY_OR_ESCALATE": ["retry_refund", "escalate_refund_failure"],
            "EVALUATE_REFUND_ELIGIBILITY": ["evaluate_refund_eligibility"],
            "NO_ACTION_NEEDED": ["no_action"],
            "NEEDS_REVIEW": ["escalate_for_review"],
        }
        return action_map.get(decision_code, ["escalate_for_review"])

    @staticmethod
    def _build_causes(decision_code: str) -> list[dict[str, Any]]:
        """Build ranked causes from the policy decision."""
        cause_map: dict[str, str] = {
            "FULL_REFUND_ENTITLED": "POLICY_FULL_REFUND",
            "PARTIAL_REFUND_OR_RESHIP": "POLICY_PARTIAL_COMPENSATION",
            "REFUND_OVERPAYMENT": "POLICY_OVERPAYMENT_REFUND",
            "REFUND_DUPLICATE": "POLICY_DUPLICATE_REFUND",
            "MONITOR_REFUND": "POLICY_REFUND_MONITORING",
            "RETRY_OR_ESCALATE": "POLICY_REFUND_RETRY",
            "NO_ACTION_NEEDED": "POLICY_NO_ISSUE",
        }
        cause_code = cause_map.get(decision_code)
        if cause_code:
            return [{"cause_code": cause_code, "rank": 1}]
        return [{"cause_code": "POLICY_REVIEW_REQUIRED", "rank": 1}]

    @staticmethod
    def _build_parties(primary_topic: str) -> list[dict[str, Any]]:
        """Determine responsible parties based on topic."""
        topic_party_map: dict[str, str] = {
            "canceled_order_paid": "platform",
            "unavailable_order_paid": "seller",
            "late_delivery_seller": "seller",
            "late_delivery_logistics": "logistics_provider",
            "payment_mismatch": "payment_provider",
            "duplicate_charge": "payment_provider",
            "refund_pending": "payment_provider",
            "refund_failed": "payment_provider",
            "valid_split_payment": "platform",
        }
        party_type = topic_party_map.get(primary_topic, "unknown")
        return [{"party_type": party_type, "party_id": None}]
