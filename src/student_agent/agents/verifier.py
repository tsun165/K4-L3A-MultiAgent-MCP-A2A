from __future__ import annotations

import copy
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from ..calibration import calculate_confidence
from ..contracts import Contracts
from ..trace import TraceWriter
from .base import EvidenceStore

ALLOWED_TOP_LEVEL_KEYS = {
    "schema_version",
    "case_id",
    "assessment",
    "affected_entities",
    "claim_assessments",
    "root_cause_analysis",
    "evidence_refs",
    "data_conflicts",
    "financial_resolution",
    "resolution_actions",
}


@dataclass
class VerificationReport:
    """Detailed invariant audit results."""

    is_valid: bool
    violations: list[str] = field(default_factory=list)
    repaired: bool = False
    downgraded: bool = False


class Verifier:
    """Independent verification agent ensuring compliance with all competition contracts."""

    name: str = "verifier"

    def __init__(self, contracts: Contracts | None = None) -> None:
        if contracts is None:
            root = Path(__file__).resolve().parents[3]
            contracts = Contracts(root / "contracts" / "schemas")
        self.contracts = contracts

    def verify_and_repair(
        self,
        output: dict[str, Any],
        store: EvidenceStore,
        trace: TraceWriter,
    ) -> dict[str, Any]:
        """Audit candidate output, repair safe inconsistencies or perform safe downgrade.

        Emits the required 'verification_completed' trace event.
        """
        repaired_output = copy.deepcopy(output)
        violations: list[str] = []
        is_repaired = False
        is_downgraded = False

        # 0. Strip any unknown top-level properties to avoid additionalProperties violation
        extra_keys = set(repaired_output.keys()) - ALLOWED_TOP_LEVEL_KEYS
        if extra_keys:
            violations.append(f"Removed extra keys: {extra_keys}")
            for k in extra_keys:
                del repaired_output[k]
            is_repaired = True

        # 1. Enforce schema_version & case_id
        repaired_output["schema_version"] = "day09-l3a-output-v2"
        if repaired_output.get("case_id") != store.case_id:
            violations.append(
                f"case_id mismatch: {repaired_output.get('case_id')} != {store.case_id}"
            )
            repaired_output["case_id"] = store.case_id
            is_repaired = True

        # 2. Check and clean evidence_refs (no foreign refs, max 30)
        orig_refs = repaired_output.get("evidence_refs", [])
        valid_refs = [r for r in orig_refs if store.owns(r)]
        if len(valid_refs) != len(orig_refs):
            violations.append(
                f"Removed {len(orig_refs) - len(valid_refs)} foreign or unverified evidence refs"
            )
            is_repaired = True
        repaired_output["evidence_refs"] = list(dict.fromkeys(valid_refs))[:30]

        # 3. Clean claim_assessments evidence_refs
        claim_assessments = repaired_output.get("claim_assessments", [])
        if isinstance(claim_assessments, list):
            cleaned_claims = []
            for claim in claim_assessments[:5]:
                c_refs = claim.get("evidence_refs", [])
                valid_c_refs = [r for r in c_refs if store.owns(r)][:30]
                if len(valid_c_refs) != len(c_refs):
                    violations.append(f"Foreign refs in claim {claim.get('claim_id')}")
                    is_repaired = True
                claim["evidence_refs"] = valid_c_refs
                cleaned_claims.append(claim)
            repaired_output["claim_assessments"] = cleaned_claims

        # 4. Check affected_entities
        entities = repaired_output.setdefault("affected_entities", {})
        entity_keys = ["order_ids", "item_ids", "seller_ids", "payment_references", "shipment_ids"]
        for key in entity_keys:
            current_vals = entities.get(key, [])
            if not isinstance(current_vals, list):
                entities[key] = []
                is_repaired = True
            else:
                entities[key] = list(dict.fromkeys(str(v) for v in current_vals if v))[:20]

        # 5. Check and repair Financial Resolution & Status Consistency
        assessment = repaired_output.setdefault("assessment", {})
        case_status = assessment.get("case_status", "needs_investigation")
        financial = repaired_output.setdefault("financial_resolution", {})
        financial["currency"] = "BRL"
        refund_lines = financial.get("refund_lines", [])

        if not isinstance(refund_lines, list):
            refund_lines = []
            financial["refund_lines"] = []
            is_repaired = True

        # Recalculate sum of refund lines using Decimal
        line_sum = Decimal("0.00")
        cleaned_lines = []
        for line in refund_lines[:10]:
            try:
                amt = Decimal(str(line.get("amount_brl", 0))).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                if amt >= 0:
                    line["amount_brl"] = float(amt)
                    line_sum += amt
                    cleaned_lines.append(line)
            except Exception:
                violations.append("Invalid amount_brl in refund line")
                is_repaired = True
        financial["refund_lines"] = cleaned_lines

        # If status is no_action, refund MUST be 0 and lines must be empty
        if case_status == "no_action":
            if financial.get("recommended_refund_brl", 0) > 0 or len(financial["refund_lines"]) > 0:
                violations.append("no_action status cannot have non-zero refund")
                financial["recommended_refund_brl"] = 0.0
                financial["refund_lines"] = []
                is_repaired = True
            else:
                financial["recommended_refund_brl"] = 0.0
        else:
            rec_amt = Decimal(str(financial.get("recommended_refund_brl", 0))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if rec_amt != line_sum:
                violations.append(
                    f"recommended_refund_brl ({rec_amt}) does not match lines sum ({line_sum})"
                )
                financial["recommended_refund_brl"] = float(line_sum)
                is_repaired = True
            else:
                financial["recommended_refund_brl"] = float(rec_amt)

        # 6. Resolution actions consistency
        actions = repaired_output.get("resolution_actions", [])
        if not isinstance(actions, list):
            actions = []
            is_repaired = True

        # Clean actions: strings 1..80, unique, max 8
        cleaned_actions: list[str] = []
        for act in actions:
            act_str = str(act).strip()[:80]
            if act_str and act_str not in cleaned_actions:
                # Remove refund actions if no_action
                if case_status == "no_action" and any(
                    k in act_str.lower() for k in ["refund", "hoan_tien", "chargeback"]
                ):
                    violations.append(f"Removed refund action '{act_str}' from no_action case")
                    is_repaired = True
                    continue
                cleaned_actions.append(act_str)

        if case_status == "action_required" and not cleaned_actions:
            violations.append("action_required case missing resolution actions")
            cleaned_actions.append("process_claim_resolution")
            is_repaired = True

        repaired_output["resolution_actions"] = cleaned_actions[:8]

        # 7. Root cause & Responsible parties consistency
        rca = repaired_output.setdefault("root_cause_analysis", {})
        ranked_causes = rca.get("ranked_causes", [])
        if isinstance(ranked_causes, list):
            valid_causes = []
            for idx, item in enumerate(ranked_causes[:5], 1):
                code = str(item.get("cause_code", "UNKNOWN_ISSUE")).upper().replace("-", "_")
                valid_causes.append({"cause_code": code, "rank": idx})
            rca["ranked_causes"] = valid_causes
        else:
            rca["ranked_causes"] = []

        resp_parties = rca.get("responsible_parties", [])
        if isinstance(resp_parties, list):
            cleaned_parties = []
            seller_ids = entities.get("seller_ids", [])
            for party in resp_parties[:5]:
                ptype = party.get("party_type", "unknown")
                pid = party.get("party_id")
                if ptype == "seller" and pid not in seller_ids:
                    if seller_ids:
                        pid = seller_ids[0]
                        violations.append(f"Aligned seller party_id to {pid}")
                        is_repaired = True
                    else:
                        ptype = "unknown"
                        pid = None
                        is_repaired = True
                cleaned_parties.append({"party_type": ptype, "party_id": pid})
            rca["responsible_parties"] = cleaned_parties

        # 8. Calibration & Downgrade Check
        try:
            self.contracts.validate_output(repaired_output, "verifier pre-validation")
        except Exception as exc:
            violations.append(f"Schema validation failure: {exc}")
            is_downgraded = True
            # Construct a pristine downgraded structure
            repaired_output = {
                "schema_version": "day09-l3a-output-v2",
                "case_id": store.case_id,
                "assessment": {
                    "primary_issue": "insufficient_evidence",
                    "case_status": "needs_investigation",
                    "confidence": 0.20,
                },
                "affected_entities": {
                    "order_ids": entities.get("order_ids", []),
                    "item_ids": entities.get("item_ids", []),
                    "seller_ids": entities.get("seller_ids", []),
                    "payment_references": entities.get("payment_references", []),
                    "shipment_ids": entities.get("shipment_ids", []),
                },
                "claim_assessments": [],
                "root_cause_analysis": {
                    "ranked_causes": [{"cause_code": "INSUFFICIENT_EVIDENCE", "rank": 1}],
                    "responsible_parties": [{"party_type": "unknown", "party_id": None}],
                },
                "evidence_refs": [],
                "data_conflicts": [],
                "financial_resolution": {
                    "currency": "BRL",
                    "recommended_refund_brl": 0.0,
                    "refund_lines": [],
                },
                "resolution_actions": ["manual_investigation_required"],
            }

        # Recalibrate confidence if not downgraded
        if not is_downgraded:
            current_conf = assessment.get("confidence", 0.85)
            has_conflicts = len(repaired_output.get("data_conflicts", [])) > 0
            calibrated = calculate_confidence(
                primary_issue=assessment.get("primary_issue", "insufficient_evidence"),
                top_score=current_conf,
                has_conflicts=has_conflicts,
                is_repaired=is_repaired,
            )
            assessment["confidence"] = calibrated

        # 9. Emit verification_completed trace event
        decision_code = "DOWNGRADED" if is_downgraded else ("REPAIRED" if is_repaired else "PASS")
        trace.emit(
            case_id=store.case_id,
            event_type="verification_completed",
            actor=self.name,
            decision_code=decision_code,
            attributes={"violations_count": len(violations)},
        )

        # Final assertion against public schema
        self.contracts.validate_output(repaired_output, "verifier finalized output")
        return repaired_output
