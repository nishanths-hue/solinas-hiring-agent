"""
JD Generation Agent — Section 12 of the operating design doc.

Triggered once a hiring request (role) is submitted. Generates internal
and external hiring assets. Nothing here auto-publishes (Section 14) —
this agent only produces drafts and writes them back onto the role record
with status 'Generated'; a human moves them through Under Review -> Approved -> Posted.
"""

import json
import os
from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """You are the JD/hiring-asset generation agent for Solinas's hiring system.
You produce factual, non-exaggerated hiring collateral strictly from the structured role
data given to you. Never invent skills, comp figures, or company claims not present in the input.
Always return valid JSON matching the requested schema, nothing else."""


def generate_hiring_assets(role: dict) -> dict:
    """
    role: dict with keys role_title, department, experience_range, mandatory_skills,
          nice_to_have_skills, business_need, kpi_expectations, hiring_priority.

    Returns dict with internal_assets + external_assets per Section 12.
    """
    user_prompt = f"""
Role data:
{json.dumps(role, indent=2)}

Generate the following as a single JSON object:
{{
  "internal_assets": {{
    "job_description": "...",
    "internal_approval_summary": "3-4 sentence summary for the approver",
    "suggested_evaluation_areas": ["..."],
    "suggested_interview_focus_areas": ["..."],
    "suggested_assignment_brief": "one paragraph, or null if role doesn't warrant an assignment",
    "suggested_reference_questions": ["..."]
  }},
  "external_assets": {{
    "linkedin_post": "...",
    "naukri_post": "...",
    "indeed_post": "...",
    "whatsapp_hiring_forward": "short, casual, forwardable text",
    "employee_referral_message": "short message an employee can send a friend"
  }}
}}
"""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=3000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = resp.content[0].text.strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)


if __name__ == "__main__":
    sample_role = {
        "role_title": "Automation Engineer",
        "department": "Engineering",
        "experience_range": "2-4 years",
        "mandatory_skills": ["PLC programming", "SCADA", "Industrial automation"],
        "nice_to_have_skills": ["Python", "IoT protocols"],
        "business_need": "Scaling factory automation for 3 new client sites",
        "kpi_expectations": "Reduce commissioning time per site by 20%",
        "hiring_priority": "High",
    }
    print(json.dumps(generate_hiring_assets(sample_role), indent=2))
