"""
Resume Screening Agent — Section 18 of the operating design doc.

Contract this agent must honor (from the doc, non-negotiable):
  - Output MUST always be explainable: recruiters must see why a score was
    assigned, matched skills, missing areas, and reasons for priority.
  - This agent recommends; it never rejects/advances a candidate on its own.
    Section 2: "AI should assist... Humans should make decisions."
  - Output feeds recruiter_screening_notes (Section 19), it does not replace it.

Resume parsing: RChilli (Section 18's open vendor decision, resolved Aug 26,
2026 — see agents/rchilli_client.py). If RChilli parsing fails for any reason
(bad file, API outage, missing key), this falls back to raw-text scoring
rather than blocking the whole screening call — but the fallback is never
silent: parsing_status in the result tells the recruiter which path was used,
since scoring off structured fields vs. raw text is a real quality difference
worth knowing about, not something to paper over.
"""

import json
import os
from anthropic import Anthropic
from agents.rchilli_client import parse_resume_file, RChilliError

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


def parse_resume(resume_text: str = None, file_bytes: bytes = None, filename: str = None) -> dict:
    """
    Prefers RChilli structured parsing when a file is provided. Falls back to
    raw text (either because only text was given, or because RChilli failed)
    with parsing_status set so callers/recruiters know which happened.
    """
    if file_bytes is not None and filename:
        try:
            parsed = parse_resume_file(file_bytes, filename)
            parsed["parsing_status"] = "rchilli_structured"
            return parsed
        except RChilliError as e:
            # Degrade, don't block — but log/surface it, not swallow it
            return {"raw_text": resume_text or "", "parsing_status": f"rchilli_failed: {e}"}
    return {"raw_text": resume_text or "", "parsing_status": "raw_text_only"}


def structured_to_text(parsed: dict) -> str:
    """
    Converts RChilli's structured parse output into a readable text blob for
    storage in candidate.resume_text (a plain string column) and for the
    screening prompt to consume. Keeps the structure legible rather than
    dumping raw JSON, since a recruiter may read this directly too.
    """
    lines = []
    if parsed.get("candidate_name"):
        lines.append(f"Name: {parsed['candidate_name']}")
    if parsed.get("total_experience_years"):
        lines.append(f"Total experience: {parsed['total_experience_years']} years")
    if parsed.get("current_employer"):
        lines.append(f"Current employer: {parsed['current_employer']}")
    if parsed.get("skills"):
        lines.append(f"Skills: {', '.join(parsed['skills'])}")
    if parsed.get("experience_history"):
        lines.append("\nExperience:")
        for e in parsed["experience_history"]:
            lines.append(f"  - {e.get('designation', '?')} at {e.get('employer', '?')} ({e.get('duration', '?')})")
    if parsed.get("education"):
        lines.append("\nEducation:")
        for ed in parsed["education"]:
            lines.append(f"  - {ed.get('degree', '?')}, {ed.get('institution', '?')}")
    text = "\n".join(lines)
    return text if text.strip() else parsed.get("raw_text", "")


def screen_candidate(resume_text: str = None, role: dict = None,
                      file_bytes: bytes = None, filename: str = None) -> dict:
    """
    role: dict with role_title, mandatory_skills, nice_to_have_skills,
          experience_range, business_need.

    Pass file_bytes + filename to use RChilli structured parsing; pass only
    resume_text to score off raw text (current default — see note below).

    Returns dict matching resume_screening_results columns:
      fit_score, matched_skills, missing_skills, risk_flags,
      suggested_probe_areas, suggested_priority, score_explanation
    """
    parsed = parse_resume(resume_text=resume_text, file_bytes=file_bytes, filename=filename)

    if parsed["parsing_status"] == "rchilli_structured":
        candidate_section = f"CANDIDATE RESUME (structured, via RChilli):\n{json.dumps(parsed, indent=2)}"
    else:
        candidate_section = f"CANDIDATE RESUME (raw text — {parsed['parsing_status']}):\n{parsed['raw_text']}"

    user_prompt = f"""
ROLE REQUIREMENTS:
{json.dumps(role, indent=2)}

{candidate_section}

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
    result["parsing_status"] = parsed["parsing_status"]
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
