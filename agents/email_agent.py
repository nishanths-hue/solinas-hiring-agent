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
