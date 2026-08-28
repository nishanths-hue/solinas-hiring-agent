"""
Priority 5 — the one shared call site every candidate-facing email trigger
uses, so "send it" and "log it" can never drift apart (no router
accidentally sends without logging, or logs without actually sending).
Lives here rather than inside any one router, since it's called from
four different ones (candidate_lifecycle, assignments, interview_scheduling,
public_application).
"""

from sqlalchemy.orm import Session
from app.models import Communication
from agents.email_agent import send_candidate_email


def send_and_log(db: Session, candidate_id: int, to_email: str, comm_type: str, subject: str, html: str):
    """
    Never raises — a failed candidate email should never block the real
    business action (a stage transition, an assignment send, a scheduled
    interview) that triggered it. The failure is recorded honestly in the
    Communication log (status="Failed"), not silently dropped and not
    allowed to break the caller.
    """
    if not to_email:
        # No email on file — log this as a real gap, not silence. A
        # recruiter reviewing communication history should see WHY nothing
        # was sent, not just an absence of any record at all.
        db.add(Communication(candidate_id=candidate_id, comm_type=comm_type, channel="Email",
                              subject=subject, message=html, status="Failed — no email on file"))
        db.commit()
        return False

    try:
        sent = send_candidate_email(to_email, subject, html)
    except Exception:
        sent = False

    db.add(Communication(
        candidate_id=candidate_id, comm_type=comm_type, channel="Email",
        subject=subject, message=html, status="Sent" if sent else "Failed",
    ))
    db.commit()
    return sent
