"""
Section 23's AI Output Contract, applied uniformly across every AI-generated
endpoint (resume screening, interview briefing, JD generation, reference
summarization).

Design choice: this ADDS standard metadata fields to whatever an AI agent
already returns, rather than wrapping it in a new envelope. The existing
field names (fit_score, matched_skills, score_explanation, etc.) already
satisfy the document's intent — evidence used, key matched/missing info —
under domain-specific names that are clearer than a generic rename would
be. Restructuring every response into {output: {...}, metadata: {...}}
would be truer to the letter of "AI Output Contract" as a wrapper, but it
would break every place the frontend currently reads a field like
result.fit_score directly, across code that's already been tested
repeatedly. This gets the substance (traceability, model version, actor,
timestamp) without the regression risk.

"Confidence" is deliberately NOT force-added everywhere — only endpoints
whose underlying agent already produces a confidence-like signal (e.g.
screening's fit_score) carry one. Fabricating a fake confidence number for
endpoints that don't have one would be worse than omitting it.
"""

from datetime import datetime, timezone

# Matches the actual model these agents call — update this in one place if
# the model changes, rather than hunting through 4 files.
AI_MODEL_VERSION = "claude-sonnet-4-6"


def wrap_ai_output(output: dict, triggered_by: str) -> dict:
    """
    Adds the contract's metadata fields to an AI-generated response dict.
    Every key already in `output` is preserved unchanged — this only adds
    new keys, never removes or renames existing ones.
    """
    return {
        **output,
        "ai_generated": True,
        "ai_model": AI_MODEL_VERSION,
        "ai_generated_at": datetime.now(timezone.utc).isoformat(),
        "ai_triggered_by": triggered_by,
    }
