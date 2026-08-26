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

    subuser_id = os.environ.get("RCHILLI_SUBUSER_ID")
    if subuser_id:
        payload["subuserid"] = subuser_id
    # If RCHILLI_SUBUSER_ID isn't set, we still send the request without it —
    # some RChilli accounts don't require it. If this account does (confirmed
    # Aug 26, 2026: it does — RChilli returned errorcode 1002 "SubUserId is
    # required" without it), the request will fail clearly rather than silently,
    # same as the missing-userkey case above.

    resp = requests.post(RCHILLI_URL, json=payload, timeout=30)
    if resp.status_code != 200:
        raise RChilliError(f"RChilli returned HTTP {resp.status_code}: {resp.text[:500]}")

    data = resp.json()

    # RChilli wraps the actual parsed fields under ResumeParserData in its
    # documented response contract. Using `.get(...) or None`-style handling
    # here too: a key present with an explicit null is treated the same as
    # a missing key, not passed through to the field-extraction step.
    parsed = data.get("ResumeParserData")
    if not parsed:
        raise RChilliError(f"Unexpected RChilli response shape — no usable 'ResumeParserData': {str(data)[:500]}")

    return _extract_screening_fields(parsed)


def _stringify(value, *keys):
    """
    Handles a real discovered inconsistency in RChilli's response: some
    nested fields (confirmed: JobPeriod) come back as a plain string rather
    than the {"FormattedX": "..."} object shape other fields use. Rather
    than guess which fields are which shape, this treats every field
    defensively — if it's a string, use it directly; if it's a dict, try
    the given candidate keys in order; anything else (including None)
    returns None instead of raising.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for k in keys:
            v = value.get(k)
            if v:
                return v
    return None


def _extract_screening_fields(parsed: dict) -> dict:
    """Pulls out only what the screening agent actually needs, in a stable
    shape — insulates resume_screening_agent.py from RChilli's exact response
    schema, so a future RChilli API version change means editing one function,
    not every caller.

    Defensive against BOTH explicit nulls AND type inconsistencies — RChilli's
    real response mixes nested-object fields with plain-string fields
    unpredictably (confirmed Aug 26, 2026: JobPeriod is a plain string, not
    the {"FormattedDuration": ...} shape other date/duration fields use).
    _stringify() above handles both shapes for every field, rather than
    assuming a fixed schema per field.
    """
    skills = parsed.get("SkillKeywords") or ""
    segments = parsed.get("SegregatedSkills") or []
    experience = parsed.get("SegregatedExperience") or []
    education = parsed.get("SegregatedQualification") or []

    def safe_experience_entry(e):
        e = e or {}
        if not isinstance(e, dict):
            return {"employer": None, "designation": None, "duration": _stringify(e)}
        return {
            "employer": _stringify(e.get("Employer"), "EmployerName", "Name"),
            "designation": _stringify(e.get("JobProfile"), "FormattedName", "Name"),
            "duration": _stringify(e.get("JobPeriod"), "FormattedDuration"),
        }

    def safe_education_entry(ed):
        ed = ed or {}
        if not isinstance(ed, dict):
            return {"institution": None, "degree": _stringify(ed)}
        return {
            "institution": _stringify(ed.get("Institution"), "Name"),
            "degree": _stringify(ed.get("Degree"), "NormalizeDegree", "FormattedDegree"),
        }

    first_exp = experience[0] if experience else {}
    first_employer = _stringify((first_exp or {}).get("Employer") if isinstance(first_exp, dict) else None,
                                 "EmployerName", "Name")

    return {
        "candidate_name": _stringify(parsed.get("Name"), "FormattedName", "Name"),
        "total_experience_years": _stringify(parsed.get("WorkedPeriod"), "TotalExperienceInYear"),
        "current_employer": first_employer,
        "skills": [s.strip() for s in skills.split(",") if s.strip()] if skills else [],
        "skills_detail": segments,
        "experience_history": [safe_experience_entry(e) for e in experience],
        "education": [safe_education_entry(ed) for ed in education],
        "raw_text": parsed.get("PlainText") or "",
    }
