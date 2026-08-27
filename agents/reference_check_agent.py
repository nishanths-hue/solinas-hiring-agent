"""
Reference Check Agent — Section 22 of the operating design doc.

Two distinct AI functions, kept separate since they happen at different times
in the workflow:
  1. generate_reference_questions — BEFORE the call, tailored to this specific
     candidate's role and any concerns raised earlier in the pipeline (not a
     generic reference-check script).
  2. summarize_reference_response — AFTER the call, turns a recruiter's raw
     notes into a structured summary + suggested risk level.

Section 10/38 constraint this respects: reference data is restricted to
leadership + recruitment. This agent doesn't enforce that itself — the router
does — but it's worth stating here too: nothing in this file's output is
appropriate to show a hiring_manager or interviewer.

The AI suggests; it does not decide. risk_level and rehire_eligibility are
labeled "suggested" in the return value and the router stores them as
AI-suggested fields a human can override, never as an automatic verdict.
"""

import json
import os
from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

QUESTIONS_SYSTEM_PROMPT = """You are the reference-check question generation agent
for Solinas's hiring system. Given a candidate's role and any concerns raised during
the hiring process, generate specific reference-check questions — not a generic
script. Prioritize questions that would confirm or resolve the stated concerns.
Return valid JSON only."""

SUMMARY_SYSTEM_PROMPT = """You are the reference-check summarization agent for
Solinas's hiring system. Given raw notes from a reference call, produce a structured
summary. Do not invent details not present in the notes. Distinguish clearly between
what the reference said (fact) and your own inference (labeled as such). Suggest a
risk level, but the recruiter has final judgment — frame it as a suggestion.
Return valid JSON only."""


def generate_reference_questions(role_title: str, prior_concerns: list[str]) -> dict:
    user_prompt = f"""
ROLE: {role_title}
CONCERNS RAISED DURING HIRING PROCESS (interviewer/recruiter notes, may be empty):
{json.dumps(prior_concerns, indent=2)}

Generate 6-8 reference-check questions. Include standard coverage (work quality,
reliability, teamwork) AND, if concerns were raised, 2-3 questions specifically
targeted at confirming or resolving those concerns without leading the reference.

Return this JSON schema:
{{
  "standard_questions": ["...", ...],
  "concern_targeted_questions": ["...", ...]
}}
"""
    resp = client.messages.create(
        model=MODEL, max_tokens=1200,
        system=QUESTIONS_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = resp.content[0].text.strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)


def summarize_reference_response(raw_notes: str) -> dict:
    user_prompt = f"""
RAW REFERENCE CALL NOTES:
{raw_notes}

Return this JSON schema:
{{
  "ai_summary": "3-5 sentence factual summary of what the reference said",
  "positive_signals": "specific positives mentioned, or null if none",
  "concerns": "specific concerns mentioned, or null if none",
  "overall_outcome": "Strong Positive" | "Positive" | "Mixed" | "Negative",
  "suggested_rehire_eligibility": "Yes" | "No" | "Unclear" | "Not Asked",
  "suggested_risk_level": "Low" | "Medium" | "High"
}}
"""
    resp = client.messages.create(
        model=MODEL, max_tokens=1000,
        system=SUMMARY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = resp.content[0].text.strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    result = json.loads(text)
    result["model_used"] = MODEL
    return result


if __name__ == "__main__":
    print(json.dumps(generate_reference_questions(
        "Automation Engineer",
        ["Seemed hesitant discussing team conflict scenario in interview"],
    ), indent=2))
