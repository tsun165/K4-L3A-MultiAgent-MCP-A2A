from __future__ import annotations

# Calibration configuration constants for deterministic confidence calculation
CONFIDENCE_HIGH_BASE = 0.90
CONFIDENCE_MAX = 0.95
CONFIDENCE_CONFLICT = 0.60
CONFIDENCE_INSUFFICIENT = 0.25
CONFIDENCE_DOWNGRADED = 0.20
REPAIR_PENALTY = 0.10
MARGIN_WEIGHT = 0.10


def calculate_confidence(
    primary_issue: str,
    top_score: float,
    second_score: float = 0.0,
    has_conflicts: bool = False,
    is_repaired: bool = False,
    is_downgraded: bool = False,
    has_insufficient_evidence: bool = False,
) -> float:
    """Calculate deterministic calibrated confidence score between 0.0 and 1.0.

    Adheres strictly to scoring policy requirements.
    """
    if is_downgraded or primary_issue == "insufficient_evidence" or has_insufficient_evidence:
        return CONFIDENCE_DOWNGRADED if is_downgraded else CONFIDENCE_INSUFFICIENT

    if has_conflicts:
        base = CONFIDENCE_CONFLICT
    else:
        margin = max(0.0, top_score - second_score)
        base = min(CONFIDENCE_MAX, CONFIDENCE_HIGH_BASE + (margin * MARGIN_WEIGHT))

    if is_repaired:
        base = max(0.30, base - REPAIR_PENALTY)

    # Round to 2 decimal places within [0.0, 1.0]
    return float(round(max(0.0, min(1.0, base)), 2))
