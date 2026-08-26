"""
Orchestrator — demonstrates the agent chain end to end against the real
schema. This is intentionally a script, not a framework: the doc's own
philosophy (Section 2) is "AI assists, humans decide" — so the orchestrator
stops and writes state to the DB at each human checkpoint rather than
auto-advancing the candidate.

Run:
    export ANTHROPIC_API_KEY=sk-...
    python orchestrator.py
"""

import json
import sys
from db import init_db, insert, fetch_one, log_activity
from agents.jd_agent import generate_hiring_assets
from agents.resume_screening_agent import screen_candidate
from agents.sla_agent import start_sla_clock, evaluate_open_clocks


def create_role(role_data: dict) -> int:
    role_id = insert("roles", {
        **role_data,
        "mandatory_skills": role_data.get("mandatory_skills", []),
        "nice_to_have_skills": role_data.get("nice_to_have_skills", []),
        "suggested_interviewers": role_data.get("suggested_interviewers", []),
    })
    # Section 27: hiring request review SLA starts the moment a request is submitted
    start_sla_clock("role", role_id, "Hiring request review")
    print(f"[intake] role #{role_id} created, stage=Draft Request, SLA clock started")
    return role_id


def run_jd_generation(role_id: int):
    role = fetch_one("SELECT * FROM roles WHERE id = ?", (role_id,))
    role_payload = {
        "role_title": role["role_title"],
        "department": role["department"],
        "experience_range": role["experience_range"],
        "mandatory_skills": json.loads(role["mandatory_skills"] or "[]"),
        "nice_to_have_skills": json.loads(role["nice_to_have_skills"] or "[]"),
        "business_need": role["business_need"],
        "kpi_expectations": role["kpi_expectations"],
        "hiring_priority": role["hiring_priority"],
    }
    assets = generate_hiring_assets(role_payload)

    with __import__("db").get_conn() as conn:
        conn.execute(
            "UPDATE roles SET jd = ? WHERE id = ?",
            (assets["internal_assets"]["job_description"], role_id),
        )
    print(f"[jd_agent] JD + hiring assets generated for role #{role_id} (status: Generated, pending review — Section 14)")
    return assets


def add_candidate(role_id: int, name: str, resume_text: str) -> int:
    candidate_id = insert("candidates", {
        "full_name": name,
        "role_id": role_id,
        "resume_text": resume_text,
        "candidate_source": "LinkedIn",
        "stage": "Applied",
    })
    log_activity(candidate_id, "Applied", stage_to="Applied", actor="system")
    return candidate_id


def run_resume_screening(candidate_id: int, role_id: int):
    candidate = fetch_one("SELECT * FROM candidates WHERE id = ?", (candidate_id,))
    role = fetch_one("SELECT * FROM roles WHERE id = ?", (role_id,))

    role_payload = {
        "role_title": role["role_title"],
        "mandatory_skills": json.loads(role["mandatory_skills"] or "[]"),
        "nice_to_have_skills": json.loads(role["nice_to_have_skills"] or "[]"),
        "experience_range": role["experience_range"],
    }

    result = screen_candidate(candidate["resume_text"], role_payload)

    insert("resume_screening_results", {
        "candidate_id": candidate_id,
        "role_id": role_id,
        **result,
    })

    with __import__("db").get_conn() as conn:
        conn.execute("UPDATE candidates SET stage = 'Resume Review' WHERE id = ?", (candidate_id,))

    log_activity(candidate_id, "AI resume screening completed", stage_from="Applied",
                 stage_to="Resume Review", actor="resume_screening_agent")

    print(f"[resume_screening_agent] candidate #{candidate_id}: "
          f"fit_score={result['fit_score']}, priority={result['suggested_priority']}")
    print(f"    explanation: {result['score_explanation']}")
    return result


def demo():
    init_db(reset=True)

    role_id = create_role({
        "role_title": "Automation Engineer",
        "department": "Engineering",
        "hiring_manager": "Priya Sharma",
        "hiring_priority": "High",
        "experience_range": "2-4 years",
        "mandatory_skills": ["PLC programming", "SCADA", "Industrial automation"],
        "nice_to_have_skills": ["Python", "IoT protocols"],
        "business_need": "Scaling factory automation for 3 new client sites",
        "kpi_expectations": "Reduce commissioning time per site by 20%",
        "replacement_or_new": "New Role",
    })

    run_jd_generation(role_id)

    candidate_id = add_candidate(
        role_id, "Rakesh Kumar",
        """3 years experience as an Automation Engineer at TechFab Industries.
        Worked extensively with Siemens PLCs and SCADA systems for factory floor
        automation. Led commissioning of 4 production lines. Familiar with Python
        for basic data logging scripts. No formal IoT protocol experience.
        Notice period: 30 days.""",
    )

    run_resume_screening(candidate_id, role_id)

    changed = evaluate_open_clocks()
    print(f"[sla_agent] {len(changed)} SLA clocks changed escalation level this run")


if __name__ == "__main__":
    if "--no-api" in sys.argv:
        print("Schema + DB layer only (no API key required):")
        init_db(reset=True)
        rid = create_role({
            "role_title": "Automation Engineer", "department": "Engineering",
            "hiring_manager": "Priya Sharma", "hiring_priority": "High",
            "experience_range": "2-4 years", "business_need": "demo",
            "kpi_expectations": "demo", "replacement_or_new": "New Role",
        })
        print(f"Role created: {fetch_one('SELECT * FROM roles WHERE id=?', (rid,))}")
    else:
        demo()
