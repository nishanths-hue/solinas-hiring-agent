"""
Pre-Interview Briefing Agent — Section 20 of the operating design doc.

What this does NOT do, on purpose: score the candidate, recommend hire/no-hire,
or replace interviewer judgment. Section 20 is explicit that the system should
NOT rigidly define interview order or interviewer ownership — it tracks
competency coverage and hands the interviewer a briefing, nothing more.

Section 20's own list of what an interviewer should see before an interview:
  candidate summary, recruiter summary, prior observations, assignment summary,
  covered areas, unresolved areas, suggested focus questions.
This agent assembles that from structured data (no LLM needed for coverage
tracking, since it's just a set difference) and uses Claude only for the part
that's genuinely generative: turning gaps into good follow-up questions.
"""

import json
import os
from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """You are the pre-interview briefing agent for Solinas's hiring system.
Given a candidate's screening summary, recruiter notes, and which competency areas
have already been covered by prior interviewers, generate specific, non-generic
follow-up questions for the UNCOVERED areas only. Do not re-suggest questions for
areas already Well Covered. Do not make a hire/no-hire judgment — that is explicitly
not your role. Return valid JSON only."""


def build_briefing(candidate_summary: dict, role_evaluation_areas: list[str],
                    prior_interviews: list[dict]) -> dict:
    """
    candidate_summary: {resume_summary, recruiter_summary, fit_score, missing_skills}
    role_evaluation_areas: e.g. ["Technical Fundamentals", "Problem Solving", "Ownership", ...]
    prior_interviews: list of {evaluation_area, coverage_level, assessment, concerns}

    Returns: {covered_areas, unresolved_areas, suggested_focus_questions, briefing_summary}
    """
    coverage_by_area = {}
    for iv in prior_interviews:
        area = iv.get("evaluation_area")
        level = iv.get("coverage_level", "Not Covered")
        # Well Covered beats Lightly Covered beats Not Covered if multiple interviews touched the same area
        rank = {"Not Covered": 0, "Lightly Covered": 1, "Well Covered": 2}
        if area not in coverage_by_area or rank.get(level, 0) > rank.get(coverage_by_area[area], 0):
            coverage_by_area[area] = level

    covered_areas = [a for a, lvl in coverage_by_area.items() if lvl == "Well Covered"]
    unresolved_areas = [a for a in role_evaluation_areas if coverage_by_area.get(a, "Not Covered") != "Well Covered"]

    if not unresolved_areas:
        return {
            "covered_areas": covered_areas,
            "unresolved_areas": [],
            "suggested_focus_questions": [],
            "briefing_summary": "All defined evaluation areas are Well Covered by prior interviews. "
                                 "This interview can focus on validating existing signal or covering "
                                 "areas not on the original list if the interviewer sees a reason to.",
        }

    prior_concerns = [iv.get("concerns") for iv in prior_interviews if iv.get("concerns")]

    user_prompt = f"""
CANDIDATE SUMMARY:
{json.dumps(candidate_summary, indent=2)}

UNCOVERED / LIGHTLY COVERED EVALUATION AREAS:
{json.dumps(unresolved_areas, indent=2)}

CONCERNS RAISED BY PRIOR INTERVIEWERS:
{json.dumps(prior_concerns, indent=2)}

Generate 2-4 specific follow-up questions PER unresolved area, grounded in this
candidate's actual background — not generic questions that could apply to anyone.
Return this JSON schema:
{{
  "suggested_focus_questions": {{
    "<evaluation area>": ["question 1", "question 2", ...]
  }},
  "briefing_summary": "2-3 sentences orienting the interviewer: what's been validated
    so far, what's still open, and anything a prior interviewer flagged as a concern"
}}
"""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = resp.content[0].text.strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    ai_output = json.loads(text)

    return {
        "covered_areas": covered_areas,
        "unresolved_areas": unresolved_areas,
        "suggested_focus_questions": ai_output.get("suggested_focus_questions", {}),
        "briefing_summary": ai_output.get("briefing_summary", ""),
    }


if __name__ == "__main__":
    result = build_briefing(
        candidate_summary={
            "resume_summary": "3 years automation engineering, strong PLC/SCADA background",
            "recruiter_summary": "Strong technical fit, slightly hesitant about relocation",
            "fit_score": 82,
            "missing_skills": ["IoT protocols"],
        },
        role_evaluation_areas=["Technical Fundamentals", "Problem Solving", "Ownership", "Communication"],
        prior_interviews=[
            {"evaluation_area": "Technical Fundamentals", "coverage_level": "Well Covered",
             "assessment": "Strong Positive", "concerns": None},
            {"evaluation_area": "Communication", "coverage_level": "Lightly Covered",
             "assessment": "Neutral", "concerns": "Seemed hesitant discussing team conflict scenario"},
        ],
    )
    print(json.dumps(result, indent=2))
