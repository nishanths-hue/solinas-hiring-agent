"""
Resume Screening Agent — Section 18 of the operating design doc.

Contract this agent must honor (from the doc, non-negotiable):
  - Output MUST always be explainable: recruiters must see why a score was
    assigned, matched skills, missing areas, and reasons for priority.
  - This agent recommends; it never rejects/advances a candidate on its own.
    Section 2: "AI should assist... Humans should make decisions."
  - Output feeds recruiter_screening_notes (Section 19), it does not replace it.

In production, swap `parse_resume_stub` for a real parser (Affinda / RChilli / HireEZ,
as named in Section 18) — this stub exists so the pipeline is runnable without a
paid parsing API key.
"""

import json
import os
from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """You are the resume screening agent for Solinas's hiring system.
You score ONE candidate against ONE role's stated requirements. You do not know
anything about the candidate beyond what is given. You must:
- Base the fit score only on evidence in the resume text.
- Never fabricate experience, employers, or skills not present in the resume.
- Always explain the score in plain language a recruiter can defend to a hiring manager.
- Flag risk factors (job hopping, unexplained gaps, notice period conflicts) as
  observations, not conclusions — recruiters interpret risk flags, you don't judge them.
Return valid JSON only."""


def parse_resume_stub(resume_text: str) -> dict:
    """Placeholder for Affinda/RChilli/HireEZ parsing (Section 18).
    Real integration should extract structured fields BEFORE scoring so the
    scoring call is grounded in structured data, not raw text alone."""
    return {"raw_text": resume_text}


def screen_candidate(resume_text: str, role: dict) -> dict:
    """
    role: dict with role_title, mandatory_skills, nice_to_have_skills,
          experience_range, business_need.

    Returns dict matching resume_screening_results columns:
      fit_score, matched_skills, missing_skills, risk_flags,
      suggested_probe_areas, suggested_priority, score_explanation
    """
    parsed = parse_resume_stub(resume_text)

    user_prompt = f"""
ROLE REQUIREMENTS:
{json.dumps(role, indent=2)}

CANDIDATE RESUME (raw text):
{parsed['raw_text']}

Score this candidate and return this exact JSON schema:
{{
  "fit_score": <integer 0-100>,
  "matched_skills": ["..."],
  "missing_skills": ["..."],
  "risk_flags": ["..."],
  "suggested_probe_areas": ["specific questions an interviewer should ask to close gaps"],
  "suggested_priority": "Priority" | "Review" | "Low Priority" | "Reject",
  "score_explanation": "2-4 sentences a recruiter could paste into a note, explaining
    the score, the strongest matches, and the biggest gap or risk"
}}

Priority bucket logic (Section 19 of the design doc):
- Priority: strong fit, fast-track
- Review: needs manual review
- Low Priority: weak fit but potentially usable
- Reject: clear mismatch
"""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = resp.content[0].text.strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    result = json.loads(text)
    result["model_used"] = MODEL
    return result


if __name__ == "__main__":
    sample_role = {
        "role_title": "Automation Engineer",
        "mandatory_skills": ["PLC programming", "SCADA", "Industrial automation"],
        "nice_to_have_skills": ["Python", "IoT protocols"],
        "experience_range": "2-4 years",
    }
    sample_resume = """
    Rakesh Kumar — 3 years experience as an Automation Engineer at TechFab Industries.
    Worked extensively with Siemens PLCs and SCADA systems for factory floor automation.
    Led commissioning of 4 production lines. Familiar with Python for basic data logging
    scripts. No formal IoT protocol experience. Notice period: 30 days.
    """
    print(json.dumps(screen_candidate(sample_resume, sample_role), indent=2))
