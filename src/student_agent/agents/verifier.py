"""Verifier agent — enforces invariants before final output."""

from __future__ import annotations

from typing import Any
from decimal import Decimal

from ..contracts import Contracts


class Verifier:
    """Checks invariants and repairs or downgrades invalid outputs."""

    def __init__(self, contracts: Contracts) -> None:
        self._contracts = contracts

    def verify_and_repair(self, output: dict[str, Any], case_id: str) -> tuple[dict[str, Any], bool, list[str]]:
        """Verify the output against rules. 
        Returns (repaired_output, is_valid, error_messages).
        """
        errors = []
        
        # 1. Check case_id matches
        if output.get("case_id") != case_id:
            errors.append(f"case_id mismatch: {output.get('case_id')} != {case_id}")
            output["case_id"] = case_id
            
        # 2. Check financial math
        fin = output.get("financial_resolution", {})
        recommended = Decimal(str(fin.get("recommended_refund_brl", 0)))
        lines = fin.get("refund_lines", [])
        line_sum = sum((Decimal(str(line.get("amount_brl", 0))) for line in lines), start=Decimal("0.00"))
        
        if recommended != line_sum:
            errors.append(f"Refund total mismatch: {recommended} != {line_sum}")
            # Safe downgrade
            output["primary_issue"] = "insufficient_evidence"
            output["case_status"] = "needs_investigation"
            fin["recommended_refund_brl"] = 0.0
            fin["refund_lines"] = []
            output["confidence"] = 0.3
            output["resolution_actions"] = []

        # 3. Status consistency
        status = output.get("case_status")
        actions = output.get("resolution_actions", [])
        
        if status == "no_action":
            if recommended > 0:
                errors.append("no_action but refund > 0")
                fin["recommended_refund_brl"] = 0.0
                fin["refund_lines"] = []
            if actions:
                errors.append("no_action but actions exist")
                output["resolution_actions"] = []
        elif status == "action_required":
            if not actions:
                errors.append("action_required but no actions")
                output["case_status"] = "needs_investigation"
                
        # 4. Check schema
        try:
            self._contracts.validate_output(output, "verifier")
        except Exception as e:
            errors.append(f"Schema error: {str(e)}")
            # Safe downgrade
            output["primary_issue"] = "insufficient_evidence"
            output["case_status"] = "needs_investigation"
            output["confidence"] = 0.3
            fin["recommended_refund_brl"] = 0.0
            fin["refund_lines"] = []
            output["resolution_actions"] = []
            
        return output, len(errors) == 0, errors
