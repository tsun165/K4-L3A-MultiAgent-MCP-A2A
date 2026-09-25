"""Specialist result interface shared by all specialist agents.

This contract defines the structured output every specialist agent must return
to the coordinator.  The coordinator merges these results into the final
L3A output JSON.

Design note: ``SpecialistResult`` is intentionally a plain ``dataclass``
(not a Pydantic model) to keep dependencies minimal — only the stdlib
``decimal`` and ``dataclasses`` modules are required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class RefundLine:
    """A single refund line item."""

    reason_code: str
    amount_brl: Decimal
    entity_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason_code": self.reason_code,
            "amount_brl": float(self.amount_brl.quantize(Decimal("0.01"))),
            "entity_id": self.entity_id,
        }


@dataclass
class SpecialistResult:
    """Standard envelope returned by every specialist agent.

    Attributes:
        domain:              Agent's area of expertise (e.g. ``"payment"``,
                             ``"policy"``, ``"order"``).
        primary_issue:       One of the ``primaryIssue`` enum values from the
                             L3A output schema, or ``None`` when the agent
                             cannot determine one.
        case_status:         ``"action_required"`` | ``"no_action"`` |
                             ``"needs_investigation"`` — reflects whether
                             an action is necessary.
        confidence:          Float in [0, 1].  Set conservatively.
        evidence_refs:       Evidence refs consumed by this agent
                             (only ``ev_*`` tokens from ``EvidenceStore``).
        resolution_actions:  Unique action strings, max 8, each ≤ 80 chars.
        refund_lines:        Itemised refund breakdown.  The coordinator
                             checks that ``sum(amount_brl) ==
                             recommended_refund_brl``.
        recommended_refund_brl:
                             Total recommended refund in BRL.  Must equal
                             the sum of ``refund_lines`` amounts.
        payment_references:  Payment IDs consumed as entities.
        ranked_causes:       List of ``{"cause_code": ..., "rank": ...}``
                             contributing to root cause analysis.
        responsible_parties: List of ``{"party_type": ..., "party_id": ...}``.
        data_conflicts:      Detected data conflicts, max 5.
        extra:               Free-form dict for inter-agent communication
                             (e.g. passing totals to another agent).
    """

    domain: str
    primary_issue: str | None = None
    case_status: str = "needs_investigation"
    confidence: float = 0.0

    # Evidence
    evidence_refs: list[str] = field(default_factory=list)

    # Resolution
    resolution_actions: list[str] = field(default_factory=list)
    refund_lines: list[RefundLine] = field(default_factory=list)
    recommended_refund_brl: Decimal = Decimal("0.00")

    # Entities
    payment_references: list[str] = field(default_factory=list)

    # Root cause
    ranked_causes: list[dict[str, Any]] = field(default_factory=list)
    responsible_parties: list[dict[str, Any]] = field(default_factory=list)

    # Conflicts
    data_conflicts: list[dict[str, Any]] = field(default_factory=list)

    # Inter-agent data
    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Validation helpers
    # ------------------------------------------------------------------ #

    def validate(self) -> list[str]:
        """Return a list of validation error messages (empty == valid)."""
        errors: list[str] = []

        # Refund total consistency
        line_sum = sum(line.amount_brl for line in self.refund_lines)
        if line_sum != self.recommended_refund_brl:
            errors.append(
                f"refund total mismatch: "
                f"recommended_refund_brl={self.recommended_refund_brl} "
                f"!= sum(refund_lines)={line_sum}"
            )

        # Unique resolution actions
        if len(self.resolution_actions) != len(set(self.resolution_actions)):
            errors.append("resolution_actions contains duplicates")

        # Confidence range
        if not 0.0 <= self.confidence <= 1.0:
            errors.append(f"confidence {self.confidence} out of [0, 1]")

        # Status / action consistency
        if self.case_status == "no_action" and self.recommended_refund_brl > 0:
            errors.append("no_action status but refund > 0")

        return errors

    # ------------------------------------------------------------------ #
    # Serialisation helpers
    # ------------------------------------------------------------------ #

    def financial_resolution_dict(self) -> dict[str, Any]:
        """Return the ``financial_resolution`` block for the output JSON."""
        return {
            "currency": "BRL",
            "recommended_refund_brl": float(
                self.recommended_refund_brl.quantize(Decimal("0.01"))
            ),
            "refund_lines": [line.to_dict() for line in self.refund_lines],
        }
