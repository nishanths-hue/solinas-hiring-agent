from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from typing import List
import secrets

from app.models import User, get_db
from app.auth import (
    hash_password, authenticate_user, create_access_token,
    require_roles, get_current_user, VALID_ROLES,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Recruitment can provision the two "operational" role types without needing
# leadership every time — this is a deliberate delegation, not a loosening
# of everything: recruitment still cannot create another leadership or
# recruitment account, only hiring_manager/interviewer. Leadership can
# create any of the four.
RECRUITMENT_CREATABLE_ROLES = {"hiring_manager", "interviewer"}


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str
    role: str  # leadership | recruitment | hiring_manager | interviewer


class BulkUserEntry(BaseModel):
    email: EmailStr
    full_name: str
    role: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    token = create_access_token({"sub": user.email, "role": user.role})
    return Token(access_token=token)


def _check_creation_permission(requester_role: str, target_role: str):
    """
    Shared by single and bulk creation so the rule can't drift between the
    two paths. Leadership can create any role; recruitment can only create
    the two operational role types, never another leadership or recruitment
    account — that boundary is the actual security-relevant part of this
    delegation, not just a convenience.
    """
    if requester_role == "leadership":
        return
    if requester_role == "recruitment" and target_role in RECRUITMENT_CREATABLE_ROLES:
        return
    raise HTTPException(
        403,
        f"Role '{requester_role}' cannot create a '{target_role}' account. "
        f"Leadership can create any role; recruitment can only create: {sorted(RECRUITMENT_CREATABLE_ROLES)}.",
    )


@router.post("/users", status_code=201)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    # First-run bootstrap is handled separately (see scripts/create_first_admin.py)
    # since there's no leadership account to authorize the very first one.
    current_user: User = Depends(require_roles("leadership", "recruitment")),
):
    if payload.role not in VALID_ROLES:
        raise HTTPException(400, f"role must be one of {VALID_ROLES}")
    _check_creation_permission(current_user.role, payload.role)
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(400, "A user with that email already exists")

    user = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "email": user.email, "role": user.role}


@router.post("/users/bulk", status_code=201)
def create_users_bulk(
    entries: List[BulkUserEntry],
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("leadership", "recruitment")),
):
    """
    Provisions multiple accounts in one call. No email is sent — this app
    has no email-sending capability at all — so a random password is
    generated per account and returned directly in the response. Whoever
    calls this is responsible for getting each password to the right
    person through some other channel (Slack, WhatsApp, in person). That's
    a real limitation worth being honest about, not a finished invite flow.

    Same-role rejects the WHOLE batch, not a partial success, so the caller
    isn't left guessing which accounts actually got created — same pattern
    as the role-requirements endpoint's atomic all-or-nothing behavior.
    """
    if not entries:
        raise HTTPException(422, "Provide at least one user to create.")

    for entry in entries:
        if entry.role not in VALID_ROLES:
            raise HTTPException(422, f"'{entry.role}' is not a valid role. Must be one of {VALID_ROLES}")
        _check_creation_permission(current_user.role, entry.role)

    emails = [e.email for e in entries]
    existing = db.query(User).filter(User.email.in_(emails)).all()
    if existing:
        existing_emails = [u.email for u in existing]
        raise HTTPException(400, f"These emails already have accounts, nothing was created: {existing_emails}")

    created = []
    for entry in entries:
        temp_password = secrets.token_urlsafe(9)  # ~12 readable characters
        user = User(
            email=entry.email, full_name=entry.full_name,
            hashed_password=hash_password(temp_password), role=entry.role,
        )
        db.add(user)
        created.append({"email": entry.email, "full_name": entry.full_name,
                          "role": entry.role, "temporary_password": temp_password})
    db.commit()
    return {
        "created_count": len(created),
        "accounts": created,
        "note": "Passwords are shown once, here, and not recoverable afterward — "
                "distribute them to each person now through a secure channel.",
    }


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("leadership")),
):
    """
    Leadership-only, regardless of who created the account — recruitment
    can create hiring_manager/interviewer accounts but resetting a
    password is a more sensitive action (it revokes the current holder's
    access implicitly) and stays leadership-only rather than following the
    same delegation as creation.
    """
    user = db.query(User).get(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    new_password = secrets.token_urlsafe(9)
    user.hashed_password = hash_password(new_password)
    db.commit()
    return {
        "user_id": user_id, "email": user.email,
        "temporary_password": new_password,
        "note": "Shown once, not recoverable afterward — distribute securely.",
    }


@router.get("/users")
def list_users(db: Session = Depends(get_db), user: User = Depends(require_roles("leadership", "recruitment"))):
    """Lets leadership/recruitment see who already has an account before
    attempting creation, rather than discovering a duplicate only via a
    400 error with no visibility into what already exists."""
    users = db.query(User).filter(User.is_active == True).all()  # noqa: E712
    return [{"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role} for u in users]
