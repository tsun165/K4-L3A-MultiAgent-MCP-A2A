from __future__ import annotations

from typing import Any

from ..trace import TraceWriter
from .base import EvidenceStore, SpecialistResult


class PolicyAgent:
    """Specialist Agent for Policy, Legal Rules and Resolution Actions.

    Owner: Đạt (liber72)
    Responsible for:
      - policy lookup and compliance checks
      - assessment.case_status ("action_required" / "no_action" / "needs_investigation")
      - resolution_actions list
      - emitting trace event "policy_decided"
    """

    name: str = "policy-agent"
    allowed_tools: set[str] = {
        "get_policy",
        "check_policy",
    }

    async def run(
        self,
        case: dict[str, Any],
        store: EvidenceStore,
        trace: TraceWriter,
    ) -> SpecialistResult:
        """Evaluate platform policies and determine case status and resolution actions.

        TODO(Đạt): Real logic will be implemented in Task 1b.
        """
        del case, store, trace
        return SpecialistResult(actor=self.name)
