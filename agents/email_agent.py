"""
Email delivery for account provisioning — closes the gap flagged in the
Team Overview doc ("passwords are shared manually, not sent automatically").

Uses Resend's HTTP API directly (via `requests`, already a dependency —
no new package needed for one API call). Gated entirely behind
RESEND_API_KEY: if it's not set, every function here degrades to doing
nothing and returning False, rather than raising. Callers (auth.py) treat
that as "email wasn't sent" and keep returning the password in the API
response as a fallback — never both silently failing AND hiding the
password from the person who actually needs it.

UNTESTED against the real Resend API — this sandbox's network is locked to
a fixed domain allowlist (PyPI, npm, GitHub, Anthropic's API) and
resend.com isn't on it, same limitation RChilli had. The first real send
has to happen from the deployed Render service, which has normal internet
access.
"""

import os
import requests

RESEND_API_URL = "https://api.resend.com/emails"

# Falls back to Resend's shared testing address if Solinas hasn't verified
# their own domain yet — real delivery works either way, but a verified
# @solinas.in address is what actually lands reliably and looks legitimate
# to the recipient. Set RESEND_FROM_ADDRESS once domain verification is done.
DEFAULT_FROM = "Solinas Hiring <onboarding@resend.dev>"


def _send(to_email: str, subject: str, html_body: str) -> bool:
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        return False  # not configured — caller falls back to showing the password directly

    from_address = os.environ.get("RESEND_FROM_ADDRESS", DEFAULT_FROM)
    try:
        resp = requests.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"from": from_address, "to": [to_email], "subject": subject, "html": html_body},
            timeout=10,
        )
        return resp.status_code in (200, 201)
    except requests.RequestException:
        return False  # network/timeout issue — degrade, don't crash account creation over an email failure


def send_new_account_email(to_email: str, full_name: str, role: str, temporary_password: str) -> bool:
    subject = "Your Solinas Hiring System account"
    html = f"""
    <p>Hi {full_name},</p>
    <p>An account has been created for you on the Solinas Hiring System, with the role <strong>{role}</strong>.</p>
    <p><strong>Email:</strong> {to_email}<br>
    <strong>Temporary password:</strong> {temporary_password}</p>
    <p>Log in at <a href="https://solinas-hiring-agent.onrender.com/app/">solinas-hiring-agent.onrender.com/app</a>
    and change your password after your first login if the option is available, or ask Leadership to reset it
    to something only you know.</p>
    <p>— Solinas Hiring System</p>
    """
    return _send(to_email, subject, html)


def send_password_reset_email(to_email: str, temporary_password: str) -> bool:
    subject = "Your Solinas Hiring System password was reset"
    html = f"""
    <p>Your password on the Solinas Hiring System was just reset.</p>
    <p><strong>New temporary password:</strong> {temporary_password}</p>
    <p>If you didn't request this, contact Leadership immediately.</p>
    <p>Log in at <a href="https://solinas-hiring-agent.onrender.com/app/">solinas-hiring-agent.onrender.com/app</a>.</p>
    <p>— Solinas Hiring System</p>
    """
    return _send(to_email, subject, html)


def send_sla_escalation_email(to_email: str, entity_type: str, stage_name: str, escalation_level: str, hours_overdue: float) -> bool:
    subject = f"[{escalation_level}] Hiring SLA breach: {stage_name}"
    urgency_note = "This has now blocked hiring progress and needs immediate attention." if escalation_level == "Hiring Blocked" else \
        "This is now a formal escalation." if escalation_level == "Escalation" else \
        "This is overdue and needs attention soon."
    html = f"""
    <p><strong>{escalation_level}</strong> — a hiring SLA is overdue.</p>
    <p><strong>Stage:</strong> {stage_name}<br>
    <strong>Overdue by:</strong> {round(hours_overdue, 1)} hours</p>
    <p>{urgency_note}</p>
    <p>Check the Solinas Hiring System dashboard for the specific candidate/role involved.</p>
    <p>— Solinas Hiring System</p>
    """
    return _send(to_email, subject, html)


# ---------------------------------------------------------------------------
# Priority 5 — candidate-facing communication (Section 12). Every function
# below returns (subject, html) rather than sending directly — the caller
# is responsible for both sending AND logging a Communication record,
# since these live in the router layer (which has db access), not here
# (which deliberately doesn't, matching every other function in this file).
# ---------------------------------------------------------------------------

def build_application_received_email(candidate_name: str, role_title: str) -> tuple[str, str]:
    subject = f"Application received — {role_title}"
    html = f"""
    <p>Hi {candidate_name},</p>
    <p>Thank you for applying for the <strong>{role_title}</strong> position at Solinas. We've received your application
    and our team will review it shortly.</p>
    <p>If your background is a match, we'll be in touch with next steps.</p>
    <p>— Solinas Hiring Team</p>
    """
    return subject, html


def build_shortlisted_email(candidate_name: str, role_title: str) -> tuple[str, str]:
    subject = f"You've been shortlisted — {role_title}"
    html = f"""
    <p>Hi {candidate_name},</p>
    <p>Good news — your application for <strong>{role_title}</strong> has been shortlisted. Our team will reach out
    with next steps shortly.</p>
    <p>— Solinas Hiring Team</p>
    """
    return subject, html


def build_assignment_email(candidate_name: str, role_title: str, assignment_name: str, deadline: str = None) -> tuple[str, str]:
    subject = f"Assignment for {role_title}"
    deadline_line = f"<p><strong>Please complete it by:</strong> {deadline}</p>" if deadline else ""
    html = f"""
    <p>Hi {candidate_name},</p>
    <p>As the next step for <strong>{role_title}</strong>, please complete the following assignment: <strong>{assignment_name}</strong>.</p>
    {deadline_line}
    <p>Our team will follow up once you've submitted it.</p>
    <p>— Solinas Hiring Team</p>
    """
    return subject, html


def build_interview_scheduled_email(candidate_name: str, role_title: str, scheduled_at: str) -> tuple[str, str]:
    subject = f"Interview scheduled — {role_title}"
    html = f"""
    <p>Hi {candidate_name},</p>
    <p>Your interview for <strong>{role_title}</strong> has been scheduled for <strong>{scheduled_at}</strong>.</p>
    <p>We'll share further details (format, meeting link if applicable) separately. Please reach out if this time
    doesn't work for you.</p>
    <p>— Solinas Hiring Team</p>
    """
    return subject, html


def build_rejection_email(candidate_name: str, role_title: str) -> tuple[str, str]:
    subject = f"Update on your application — {role_title}"
    html = f"""
    <p>Hi {candidate_name},</p>
    <p>Thank you for your interest in the <strong>{role_title}</strong> position and for taking the time to apply.
    After careful consideration, we've decided to move forward with other candidates for this role.</p>
    <p>We appreciate your interest in Solinas and encourage you to apply for future openings that match your background.</p>
    <p>— Solinas Hiring Team</p>
    """
    return subject, html


def send_candidate_email(to_email: str, subject: str, html: str) -> bool:
    """Thin wrapper so every candidate-facing send goes through the same
    _send() path as everything else in this file — callers use this after
    building their subject/html with one of the build_* functions above."""
    return _send(to_email, subject, html)
