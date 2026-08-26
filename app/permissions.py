"""
Field-level permission enforcement — Section 38 (edit/view matrix) and
Section 10 (restricted fields) of the operating design doc.

Deliberately implemented as response filtering, not just endpoint gating.
A 403 on an endpoint stops a Hiring Manager from hitting /roles/5/compensation,
but it does NOT stop compensation_range from leaking inside a generic
/roles/5 response if that field just happens to be in the serializer. This
module is the single place that decides what a given role is allowed to SEE,
so that guarantee holds no matter how many endpoints return a Role object.
"""

# Section 10 — restricted fields, visible to recruitment + leadership only
RESTRICTED_ROLE_FIELDS = {
    "compensation_range",
    "suggested_compensation_range",
    "offer_strategy_notes",
    "internal_risk_notes",
}

RESTRICTED_CANDIDATE_NOTE_FIELDS = {
    "compensation_alignment",  # recruiter_screening_notes field
}

# Section 38 — who can see restricted fields at all
ROLES_WITH_COMPENSATION_ACCESS = {"leadership", "recruitment"}
ROLES_WITH_REFERENCE_ACCESS = {"leadership", "recruitment"}

# Section 38 — who can edit what
ROLE_EDIT_PERMISSIONS = {
    "leadership": {"role.hiring_priority", "role.compensation_range", "role.stage",
                   "candidate.priority_override"},
    "recruitment": {"candidate.stage", "candidate.recruiter_notes", "candidate.recruiter_tags",
                     "candidate.priority_override", "role.stage", "sla.*", "offer.*",
                     "joining_risk.*"},
    "hiring_manager": {"interview.own_feedback", "candidate.recommendation",
                        "role.mandatory_skills", "role.nice_to_have_skills",
                        "role.experience_range", "role.jd", "role.hiring_notes"},
    "interviewer": {"interview.own_feedback_only"},
}


def filter_role_dict(role_dict: dict, requesting_role: str) -> dict:
    """Strip Section 10 restricted fields from a role payload unless the
    requester is leadership or recruitment."""
    if requesting_role in ROLES_WITH_COMPENSATION_ACCESS:
        return role_dict
    return {k: v for k, v in role_dict.items() if k not in RESTRICTED_ROLE_FIELDS}


def filter_recruiter_note_dict(note_dict: dict, requesting_role: str) -> dict:
    """Hiring managers and interviewers cannot see compensation_alignment
    (Section 10) or recruiter-only fields (Section 38: 'Cannot View: recruiter-only notes')."""
    if requesting_role in ("leadership", "recruitment"):
        return note_dict
    if requesting_role == "hiring_manager":
        # HM sees the summary but not compensation-linked or recruiter-internal fields
        blocked = RESTRICTED_CANDIDATE_NOTE_FIELDS | {"recruiter_recommendation"}
        return {k: v for k, v in note_dict.items() if k not in blocked}
    # interviewer: Section 38 says interviewers cannot view internal HR notes at all
    return {}


def can_edit(user_role: str, permission_key: str) -> bool:
    """permission_key examples: 'role.compensation_range', 'candidate.stage'."""
    allowed = ROLE_EDIT_PERMISSIONS.get(user_role, set())
    if permission_key in allowed:
        return True
    # wildcard support, e.g. 'sla.*' covers 'sla.escalation_level'
    prefix = permission_key.split(".")[0] + ".*"
    return prefix in allowed


def can_view_references(user_role: str) -> bool:
    return user_role in ROLES_WITH_REFERENCE_ACCESS


def can_view_compensation(user_role: str) -> bool:
    return user_role in ROLES_WITH_COMPENSATION_ACCESS
