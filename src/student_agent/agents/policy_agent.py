from __future__ import annotations

from typing import Any

from ..trace import TraceWriter
from .base import EvidenceStore, SpecialistResult
from .tools import GET_POLICY, POLICY_TOOLS


class PolicyAgent:
    """Specialist Agent for Policy lookup.

    Only fetches the authoritative policy document and returns it in
    ``findings["policy_raw"]``. It deliberately does NOT decide primary_issue,
    case_status or resolution_actions itself: those require the other
    specialists' conclusions, which this agent's frozen Specialist interface
    (``run(case, store, trace)``) does not receive. The coordinator combines
    ``policy_raw`` with the specialist results and emits the ``policy_decided``
    trace event (STANDARDS §6/§9) — this keeps the decision grounded in
    verified evidence rather than the customer's raw claim topic.
    """

    name: str = "policy-agent"
    allowed_tools: frozenset[str] = POLICY_TOOLS

    async def run(
        self,
        case: dict[str, Any],
        store: EvidenceStore,
        trace: TraceWriter,
    ) -> SpecialistResult:
        del trace  # tool_result_consumed is emitted by EvidenceStore.fetch
        result = SpecialistResult(actor=self.name)
        try:
            policy_version = case.get("policy_version", "EC_POLICY_V1")
            rec = await store.fetch(self.name, GET_POLICY, policy_version=policy_version)
            result.findings["policy_raw"] = rec.data
            result.evidence_refs.append(rec.evidence_ref)
        except Exception as exc:  # specialists never raise (STANDARDS §5)
            result.errors.append(f"{type(exc).__name__}: {exc}"[:200])
        return result
