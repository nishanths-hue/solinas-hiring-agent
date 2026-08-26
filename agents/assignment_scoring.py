"""
Assignment scoring — Section 21. Deliberately NOT an LLM call: a human
reviewer enters per-criterion scores (0-100 each), this just computes the
weighted total. Same reasoning as sla_agent.py — the math is exact, adding
an LLM here would only introduce cost and a hallucination surface.
"""

WEIGHTS = {
    "technical_accuracy_score": 0.40,
    "problem_solving_score": 0.25,
    "clarity_structure_score": 0.15,
    "practical_thinking_score": 0.10,
    "completeness_score": 0.10,
}


def compute_weighted_total(scores: dict) -> float:
    """
    scores: dict with the 5 keys in WEIGHTS, each 0-100.
    Missing keys are treated as an error, not silently skipped — a partial
    score set shouldn't produce a misleadingly precise-looking total.
    """
    missing = [k for k in WEIGHTS if k not in scores or scores[k] is None]
    if missing:
        raise ValueError(f"Missing required scores: {missing}")
    for k, v in scores.items():
        if k in WEIGHTS and not (0 <= v <= 100):
            raise ValueError(f"{k} must be between 0 and 100, got {v}")

    total = sum(scores[k] * weight for k, weight in WEIGHTS.items())
    return round(total, 2)
