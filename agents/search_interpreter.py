"""
Natural-language search interpreter — Section 20 of the new operating
design doc names this as its own capability, distinct from keyword search
(already built at GET /candidates/search/query).

Design: the LLM's ONLY job is translating free text into a STRUCTURED
filter spec — it never reads candidate data directly or ranks candidates
itself. The structured filter is then applied deterministically against
real data by the calling endpoint. This is the same pattern already used
elsewhere in this system (AI interprets/extracts, deterministic logic
executes) — and it's the only way to correctly handle something like
"haven't been contacted in 2 weeks" (the doc's own example query), which
requires computing a real timestamp comparison against activity records,
not text-matching against resume content. An LLM asked to directly judge
"who hasn't been contacted recently" by reading raw candidate data would
have no reliable way to know that without the same structured computation
happening anyway — so it's done explicitly and deterministically instead
of hoping the model gets it right from context.
"""

import json
import os
from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """You translate a recruiter's natural-language search request into a
structured filter specification for a hiring database. You do not see any
candidate data — you only produce a filter spec based on the query text.

Return valid JSON only, with this exact shape:
{
  "skills_required": [],       // skills that MUST be present (from resume/screening), empty list if none stated
  "skills_preferred": [],      // skills that are a bonus but not required
  "min_experience_years": null,  // integer or null — infer from phrases like "3+ years", "experienced" (use 3 as a reasonable floor for "experienced" if no number given), "junior" (use 0)
  "stages": [],                 // subset of: Applied, Resume Review, Shortlisted, Interview Process, Assignment Sent,
                                 // Assignment Submitted, Final Evaluation, Reference Check, Offer Discussion, Offer Released,
                                 // Offer Accepted, Joined — empty list means no stage filter (all active stages)
  "min_fit_score": null,        // integer 0-100 or null — only set if the query implies a quality bar, e.g. "strong candidates"
  "days_since_last_activity_min": null,  // integer or null — set this for phrases like "haven't been contacted in 2 weeks" (=14),
                                            // "gone quiet for a month" (=30), etc.
  "interpretation_notes": ""    // one plain-English sentence explaining how you interpreted the query, shown to the recruiter
                                 // so they can verify the interpretation matches what they meant
}

Never invent skills, stages, or numbers that aren't reasonably implied by the query text."""


def interpret_search_query(query: str) -> dict:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": query}],
    )
    text = resp.content[0].text.strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    result = json.loads(text)

    # Defaults for any field the model omits — a structurally-incomplete but
    # otherwise valid response shouldn't crash the deterministic filter step
    # that consumes this downstream.
    return {
        "skills_required": result.get("skills_required") or [],
        "skills_preferred": result.get("skills_preferred") or [],
        "min_experience_years": result.get("min_experience_years"),
        "stages": result.get("stages") or [],
        "min_fit_score": result.get("min_fit_score"),
        "days_since_last_activity_min": result.get("days_since_last_activity_min"),
        "interpretation_notes": result.get("interpretation_notes") or "",
        "model_used": MODEL,
    }
