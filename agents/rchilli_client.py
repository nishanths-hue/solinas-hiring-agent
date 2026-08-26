"""
RChilli Resume Parser client — Section 18's resume-parsing decision, resolved.

Endpoint and version supplied by Solinas (Aug 26, 2026):
  https://rest-mu.rchilli.com/RChilliParser/Rchilli/parseResumeBinary  (v8.0.0, Mumbai region)

Requires RCHILLI_USER_KEY as an environment variable — same pattern as
ANTHROPIC_API_KEY. Never hardcode the key.

IMPORTANT — untested against the live endpoint. This sandbox's network is
allowlisted to a fixed set of domains (PyPI, npm, GitHub, Anthropic's API)
and rchilli.com is not on it, so this code could not be exercised against
the real API before shipping. It's written to RChilli's documented request/
response contract, but the first real call needs to happen from the deployed
Render service — same situation the Anthropic key was in before its first
live test in production.
"""

import base64
import os
import requests

RCHILLI_URL = "https://rest-mu.rchilli.com/RChilliParser/Rchilli/parseResumeBinary"
RCHILLI_VERSION = "8.0.0"


class RChilliError(Exception):
    pass


def parse_resume_file(file_bytes: bytes, filename: str) -> dict:
    """
    Sends a resume file to RChilli and returns the structured fields relevant
    to screening. Raises RChilliError on any failure — callers should not
    silently fall back to raw-text scoring without knowing parsing failed,
    since that changes what the AI is actually reasoning over.
    """
    user_key = os.environ.get("RCHILLI_USER_KEY")
    if not user_key:
        raise RChilliError(
            "RCHILLI_USER_KEY is not set. Get it from the RChilli account dashboard "
            "and set it as an environment variable — do not hardcode it."
        )

    payload = {
        "filedata": base64.b64encode(file_bytes).decode("utf-8"),
        "filename": filename,
        "userkey": user_key,
        "version": RCHILLI_VERSION,
    }

    resp = requests.post(RCHILLI_URL, json=payload, timeout=30)
    if resp.status_code != 200:
        raise RChilliError(f"RChilli returned HTTP {resp.status_code}: {resp.text[:500]}")

    data = resp.json()

    # RChilli wraps the actual parsed fields under ResumeParserData in its
    # documented response contract. If that key is missing, something about
    # the response shape differs from what's documented — surface it clearly
    # rather than silently returning an empty/wrong structure.
    parsed = data.get("ResumeParserData")
    if parsed is None:
        raise RChilliError(f"Unexpected RChilli response shape — no 'ResumeParserData' key: {str(data)[:500]}")

    return _extract_screening_fields(parsed)


def _extract_screening_fields(parsed: dict) -> dict:
    """Pulls out only what the screening agent actually needs, in a stable
    shape — insulates resume_screening_agent.py from RChilli's exact response
    schema, so a future RChilli API version change means editing one function,
    not every caller."""
    skills = parsed.get("SkillKeywords", "")
    segments = parsed.get("SegregatedSkills", [])
    experience = parsed.get("SegregatedExperience", [])
    education = parsed.get("SegregatedQualification", [])

    return {
        "candidate_name": parsed.get("Name", {}).get("FormattedName"),
        "total_experience_years": parsed.get("WorkedPeriod", {}).get("TotalExperienceInYear"),
        "current_employer": (experience[0].get("Employer", {}).get("EmployerName")
                              if experience else None),
        "skills": [s.strip() for s in skills.split(",") if s.strip()] if skills else [],
        "skills_detail": segments,
        "experience_history": [
            {
                "employer": e.get("Employer", {}).get("EmployerName"),
                "designation": e.get("JobProfile", {}).get("FormattedName"),
                "duration": e.get("JobPeriod", {}).get("FormattedDuration"),
            }
            for e in experience
        ],
        "education": [
            {
                "institution": ed.get("Institution", {}).get("Name"),
                "degree": ed.get("Degree", {}).get("NormalizeDegree"),
            }
            for ed in education
        ],
        # kept for cases where the structured fields above miss something the
        # LLM scoring step could still pick up from context
        "raw_text": parsed.get("ResumeFileFormat") and parsed.get("PlainText", ""),
    }
