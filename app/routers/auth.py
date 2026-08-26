from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.models import User, get_db
from app.auth import (
    hash_password, authenticate_user, create_access_token,
    require_roles, VALID_ROLES,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str
    role: str  # leadership | recruitment | hiring_manager | interviewer


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


@router.post("/users", status_code=201)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    # Only leadership can provision accounts. First-run bootstrap is handled
    # separately (see scripts/create_first_admin.py) since there's no leadership
    # account to authorize the very first one.
    _current_user=Depends(require_roles("leadership")),
):
    if payload.role not in VALID_ROLES:
        raise HTTPException(400, f"role must be one of {VALID_ROLES}")
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
