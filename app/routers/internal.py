"""
Item #2 from the pending list — SLA reminders were reactive, only firing
when a human loaded the dashboard. This endpoint lets a free external
scheduler (not a paid Render Cron Job) trigger the same evaluation on a
real schedule, without needing anyone to be logged in or looking at the
dashboard at all.

Deliberately NOT behind normal user auth (get_current_user/require_roles)
— an external cron-ping service can't log in as a person. Instead it's
protected by a single shared secret, checked as a query parameter (not a
header) specifically because most free URL-scheduling services only
support plain GET requests with no custom headers — matching what's
actually usable rather than a theoretically cleaner design nobody could
call. GET is safe here despite triggering a state change (escalation
levels, emails) because it's idempotent in the sense that matters:
running it twice in a row does not double-send anything or move any
clock further than reality — see evaluate_open_clocks(), which only acts
on a genuine level transition, not on every call.
"""

import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models import get_db
from app.sla import evaluate_open_clocks

router = APIRouter(prefix="/internal", tags=["internal"])


@router.get("/sla-tick")
def sla_tick(secret: str, db: Session = Depends(get_db)):
    expected = os.environ.get("SLA_CRON_SECRET")
    if not expected:
        # Fails closed, not open — if the secret was never configured,
        # this endpoint refuses to run rather than silently operating
        # with no protection at all.
        raise HTTPException(503, "SLA_CRON_SECRET is not configured on this server.")
    if secret != expected:
        raise HTTPException(403, "Invalid secret.")

    changed = evaluate_open_clocks(db)
    return {"clocks_changed": len(changed), "details": changed}
